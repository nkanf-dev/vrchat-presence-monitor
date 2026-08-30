from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from vrchat_monitor.vrchat import VRChatError

from server.app import create_app
from server.settings import Settings
from server.storage import Store
from server.worlds import DiscoveryService, WorldResolver, WorldService


WORLD_ID = "wrld_00000000-0000-0000-0000-000000000123"
WORLD_B = "wrld_00000000-0000-0000-0000-000000000456"
WORLD_C = "wrld_00000000-0000-0000-0000-000000000789"


def as_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def seed_friend(
    store: Store,
    tenant_id: str,
    friend_id: str,
    display_name: str,
    *,
    is_self: bool = False,
    updated_at: datetime,
) -> None:
    with store.lock, store.connection() as db:
        db.execute(
            """INSERT INTO friends(
                tenant_id,id,username,display_name,is_self,status,location,updated_at
            ) VALUES(?,?,?,?,?,'offline','offline',?)""",
            (
                tenant_id,
                friend_id,
                display_name.casefold(),
                display_name,
                int(is_self),
                as_text(updated_at),
            ),
        )


def seed_tracking(
    store: Store,
    tenant_id: str,
    friend_id: str,
    occurred_at: datetime,
) -> None:
    with store.lock, store.connection() as db:
        db.execute(
            """INSERT INTO friend_tracking_events(
                tenant_id,event_id,friend_id,tracked,occurred_at,source
            ) VALUES(?,?,?,?,?,'test')""",
            (
                tenant_id,
                f"tracking-{friend_id}-{int(occurred_at.timestamp())}",
                friend_id,
                1,
                as_text(occurred_at),
            ),
        )


def seed_event(
    store: Store,
    tenant_id: str,
    friend_id: str,
    event_id: str,
    occurred_at: datetime,
    status: str,
    location: str,
) -> None:
    with store.lock, store.connection() as db:
        db.execute(
            """INSERT INTO status_events(
                tenant_id,client_event_id,friend_id,occurred_at,old_status,
                new_status,location,platform,source
            ) VALUES(?,?,?,?,'offline',?,?,?,'test')""",
            (
                tenant_id,
                event_id,
                friend_id,
                as_text(occurred_at),
                status,
                location,
                "standalonewindows" if status != "offline" else "",
            ),
        )


def seed_coverage_pair(store: Store, tenant_id: str, start: datetime) -> None:
    with store.lock, store.connection() as db:
        for offset in (0, 1):
            observed_at = start + timedelta(minutes=offset)
            db.execute(
                """INSERT INTO collection_samples(
                    tenant_id,sample_id,observed_at,source,outcome,authoritative,
                    expected_interval_seconds,friend_count,online_count,duration_ms,
                    error_category
                ) VALUES(?,?,?,'test','success',1,60,3,1,10,'')""",
                (
                    tenant_id,
                    f"sample-{int(start.timestamp())}-{offset}",
                    as_text(observed_at),
                ),
            )


class FakeTimer:
    def __init__(self):
        self.value = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


class FakeWorldFetcher:
    def __init__(self, error: Exception | None = None):
        self.calls: list[tuple[str, str]] = []
        self.error = error

    def __call__(self, tenant_id: str, world_id: str):
        self.calls.append((tenant_id, world_id))
        if self.error:
            raise self.error
        return {
            "id": world_id,
            "name": "Observed World",
            "authorName": "Builder",
            "thumbnailImageUrl": "https://api.vrchat.cloud/api/1/file/example",
        }


class BlockingWorldFetcher(FakeWorldFetcher):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, tenant_id: str, world_id: str):
        self.calls.append((tenant_id, world_id))
        self.started.set()
        if not self.release.wait(timeout=2):
            raise RuntimeError("test fetch did not resume")
        return {
            "id": world_id,
            "name": "Observed World",
            "authorName": "Builder",
        }


class HostedWorldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(str(Path(self.directory.name) / "hosted.sqlite3"))
        created = self.store.bootstrap("Alice", "collector")
        self.tenant_id = created["tenant_id"]
        self.collector_id = created["collector_id"]
        stamp = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(
            timespec="microseconds"
        )
        self.store.ingest_authoritative_snapshot(
            self.tenant_id,
            self.collector_id,
            [
                {
                    "id": "usr_alice",
                    "display_name": "Alice",
                    "status": "active",
                    "location": f"{WORLD_ID}:instance",
                    "updated_at": stamp,
                }
            ],
            [
                {
                    "client_event_id": "world-enter",
                    "friend_id": "usr_alice",
                    "occurred_at": stamp,
                    "old_status": "offline",
                    "new_status": "active",
                    "location": f"{WORLD_ID}:instance",
                }
            ],
            source="hosted-rest",
            observed_at=stamp,
            expected_interval_seconds=180,
        )

    def test_library_request_never_fetches_vrchat(self):
        fetcher = FakeWorldFetcher()
        resolver = WorldResolver(self.store, fetcher)
        service = WorldService(self.store, resolver)
        result = service.library(self.tenant_id, query=WORLD_ID, limit=20)

        self.assertEqual(result["items"][0]["id"], WORLD_ID)
        self.assertEqual(result["items"][0]["resolution_status"], "pending")
        self.assertEqual(fetcher.calls, [])
        self.assertTrue(resolver.drain_once())
        self.assertEqual(len(fetcher.calls), 1)

    def test_library_offset_takes_priority_and_cursor_remains_compatible(self):
        observed_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        seed_event(
            self.store,
            self.tenant_id,
            "usr_alice",
            "world-enter-b",
            observed_at,
            "active",
            f"{WORLD_B}:instance",
        )
        service = WorldService(self.store, WorldResolver(self.store, FakeWorldFetcher()))

        first = service.library(self.tenant_id, limit=1)
        cursor_page = service.library(
            self.tenant_id,
            cursor=first["next_cursor"],
            limit=1,
        )
        offset_page = service.library(
            self.tenant_id,
            cursor=first["next_cursor"],
            offset=0,
            limit=1,
        )

        self.assertEqual(first["items"][0]["id"], WORLD_B)
        self.assertEqual(cursor_page["items"][0]["id"], WORLD_ID)
        self.assertEqual(offset_page["items"][0]["id"], WORLD_B)
        self.assertEqual(offset_page["total"], 2)

    def test_duplicate_world_ids_queue_and_resolve_once(self):
        fetcher = FakeWorldFetcher()
        resolver = WorldResolver(self.store, fetcher)
        self.assertTrue(resolver.enqueue(self.tenant_id, WORLD_ID))
        self.assertFalse(resolver.enqueue("tenant-b", WORLD_ID))
        self.assertTrue(resolver.drain_once())
        self.assertEqual(len(fetcher.calls), 1)
        self.assertEqual(self.store.world_cache_get(WORLD_ID)["name"], "Observed World")

    def test_fresh_cache_added_after_queue_skips_duplicate_fetch(self):
        fetcher = FakeWorldFetcher()
        resolver = WorldResolver(self.store, fetcher)
        self.assertTrue(resolver.enqueue(self.tenant_id, WORLD_ID))
        self.store.world_cache_put(WORLD_ID, {"id": WORLD_ID, "name": "Warm"})

        self.assertTrue(resolver.drain_once())
        self.assertEqual(fetcher.calls, [])

    def test_detail_returns_immediately_then_background_resolution_fills_cache(self):
        fetcher = FakeWorldFetcher()
        resolver = WorldResolver(self.store, fetcher)
        service = WorldService(self.store, resolver)
        first = service.detail(self.tenant_id, WORLD_ID)

        self.assertEqual(first["id"], WORLD_ID)
        self.assertEqual(first["resolution_status"], "pending")
        self.assertEqual(fetcher.calls, [])
        resolver.drain_once()
        second = service.detail(self.tenant_id, WORLD_ID)
        self.assertEqual(second["name"], "Observed World")
        self.assertEqual(second["resolution_status"], "ready")

    def test_inflight_world_stays_single_flight(self):
        fetcher = BlockingWorldFetcher()
        resolver = WorldResolver(self.store, fetcher)
        self.assertTrue(resolver.enqueue(self.tenant_id, WORLD_ID))
        worker = threading.Thread(target=resolver.drain_once)
        worker.start()
        self.assertTrue(fetcher.started.wait(timeout=1))

        self.assertFalse(resolver.enqueue("tenant-b", WORLD_ID))
        fetcher.release.set()
        worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertFalse(resolver.drain_once())
        self.assertEqual(len(fetcher.calls), 1)

    def test_resolver_paces_upstream_requests(self):
        timer = FakeTimer()
        fetcher = FakeWorldFetcher()
        resolver = WorldResolver(
            self.store,
            fetcher,
            monotonic=timer.monotonic,
            sleeper=timer.sleep,
        )
        resolver.enqueue(self.tenant_id, WORLD_ID)
        resolver.enqueue(self.tenant_id, WORLD_B)

        self.assertTrue(resolver.drain_once())
        self.assertTrue(resolver.drain_once())

        self.assertEqual(len(fetcher.calls), 2)
        self.assertEqual(timer.sleeps, [1.0])

    def test_rate_limit_deadline_survives_resolver_restart(self):
        fetcher = FakeWorldFetcher(VRChatError("slow down", 429, 600))
        resolver = WorldResolver(self.store, fetcher)
        resolver.enqueue(self.tenant_id, WORLD_ID)
        resolver.drain_once()
        state = resolver._state(WORLD_ID)
        retry_at = datetime.fromisoformat(str(state["retry_at"]))
        self.assertGreaterEqual(
            retry_at,
            datetime.now(timezone.utc) + timedelta(seconds=590),
        )

        restarted_fetcher = FakeWorldFetcher()
        restarted = WorldResolver(self.store, restarted_fetcher)
        self.assertFalse(restarted.enqueue(self.tenant_id, WORLD_ID))
        self.assertFalse(restarted.drain_once())
        self.assertEqual(restarted_fetcher.calls, [])

    def test_refresh_failure_keeps_cached_world(self):
        self.store.world_cache_put(
            WORLD_ID,
            {"id": WORLD_ID, "name": "Known World", "author_name": "Builder"},
        )
        old = as_text(datetime.now(timezone.utc) - timedelta(days=3))
        with self.store.lock, self.store.connection() as db:
            db.execute(
                "UPDATE world_cache SET fetched_at=? WHERE world_id=?",
                (old, WORLD_ID),
            )
        fetcher = FakeWorldFetcher(VRChatError("service failed", 503))
        resolver = WorldResolver(self.store, fetcher)
        service = WorldService(self.store, resolver)

        before = service.detail(self.tenant_id, WORLD_ID)
        self.assertEqual(before["resolution_status"], "ready")
        self.assertTrue(before["stale"])
        self.assertTrue(resolver.drain_once())
        after = service.detail(self.tenant_id, WORLD_ID)

        self.assertEqual(after["name"], "Known World")
        self.assertEqual(after["resolution_status"], "ready")
        self.assertTrue(after["stale"])

    def test_not_found_uses_available_machine_states(self):
        fetcher = FakeWorldFetcher(VRChatError("missing", 404))
        resolver = WorldResolver(self.store, fetcher)
        service = WorldService(self.store, resolver)

        self.assertEqual(
            service.detail(self.tenant_id, WORLD_ID)["resolution_status"],
            "pending",
        )
        self.assertTrue(resolver.drain_once())
        result = service.detail(self.tenant_id, WORLD_ID)

        self.assertEqual(result["resolution_status"], "unavailable")

    def test_unobserved_world_is_not_queued(self):
        fetcher = FakeWorldFetcher()
        resolver = WorldResolver(self.store, fetcher)
        service = WorldService(self.store, resolver)
        with self.assertRaises(KeyError):
            service.detail(
                self.tenant_id,
                "wrld_00000000-0000-0000-0000-000000000999",
            )
        self.assertEqual(fetcher.calls, [])


class WorldHttpContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        static_dir = root / "static"
        static_dir.mkdir()
        (static_dir / "index.html").write_text(
            "<!doctype html><html><body></body></html>", encoding="utf-8"
        )
        self.settings = Settings(
            data_dir=root,
            static_dir=static_dir,
            bootstrap_token="bootstrap-secret",
            cookie_secure="never",
        )
        self.store = Store(str(root / "hosted.sqlite3"))
        created = self.store.bootstrap("World API", "collector")
        self.tenant_id = created["tenant_id"]
        observed_at = datetime.now(timezone.utc) - timedelta(minutes=2)
        seed_friend(
            self.store,
            self.tenant_id,
            "usr_world_api",
            "World API Friend",
            updated_at=observed_at,
        )
        seed_tracking(
            self.store,
            self.tenant_id,
            "usr_world_api",
            observed_at - timedelta(minutes=1),
        )
        seed_coverage_pair(self.store, self.tenant_id, observed_at)
        seed_event(
            self.store,
            self.tenant_id,
            "usr_world_api",
            "world-api-enter",
            observed_at,
            "active",
            f"{WORLD_ID}:instance",
        )
        self.store.world_cache_put(
            WORLD_ID,
            {
                "id": WORLD_ID,
                "name": "Tagged World",
                "tags": ["author_tag_social", "system_approved"],
            },
        )
        self.client = TestClient(create_app(self.settings, self.store))
        self.addCleanup(self.client.close)
        login = self.client.post(
            "/v1/login",
            headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
            json={"access_code": created["access_code"]},
        )
        self.assertEqual(login.status_code, 200, login.text)

    def test_world_tag_contract_filters_library_and_discovery(self) -> None:
        tags = self.client.get("/v1/world-tags")
        library = self.client.get(
            "/v1/world-library", params={"world_tag": "author_tag_social"}
        )
        discovery = self.client.get(
            "/v1/discovery/worlds",
            params={
                "days": 7,
                "world_tag": "author_tag_social",
                "limit": 1,
                "offset": 0,
            },
        )

        self.assertEqual(
            tags.json(),
            [
                {"name": "author_tag_social", "count": 1},
                {"name": "system_approved", "count": 1},
            ],
        )
        self.assertEqual(library.status_code, 200, library.text)
        self.assertEqual(library.json()["items"][0]["id"], WORLD_ID)
        self.assertEqual(discovery.status_code, 200, discovery.text)
        self.assertEqual(discovery.json()["hot"][0]["world_id"], WORLD_ID)
        self.assertEqual(discovery.json()["hot_total"], 1)
        self.assertEqual(discovery.json()["rising_total"], 1)
        self.assertEqual(discovery.json()["limit"], 1)
        self.assertEqual(discovery.json()["offset"], 0)

    def test_world_library_offset_is_optional_nonnegative_and_beats_cursor(self) -> None:
        response = self.client.get(
            "/v1/world-library",
            params={"offset": 0, "cursor": "MQ", "limit": 1},
        )
        invalid = self.client.get("/v1/world-library", params={"offset": -1})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["items"][0]["id"], WORLD_ID)
        self.assertEqual(invalid.status_code, 422, invalid.text)


class DiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(str(Path(self.directory.name) / "discovery.sqlite3"))
        self.current = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)

    def create_tenant(self, label: str) -> str:
        return self.store.bootstrap(label, f"collector-{label}")["tenant_id"]

    def service(self) -> tuple[DiscoveryService, FakeWorldFetcher, WorldResolver]:
        fetcher = FakeWorldFetcher()
        resolver = WorldResolver(self.store, fetcher)
        return (
            DiscoveryService(
                self.store,
                resolver,
                clock=lambda: self.current,
            ),
            fetcher,
            resolver,
        )

    def test_observation_migration_invalidates_an_empty_discovery_cache(self):
        tenant_id = self.create_tenant("migrated-evidence")
        event_time = self.current - timedelta(days=1)
        seed_friend(
            self.store,
            tenant_id,
            "usr_migrated",
            "Migrated Friend",
            updated_at=event_time,
        )
        seed_event(
            self.store,
            tenant_id,
            "usr_migrated",
            "migrated-enter",
            event_time,
            "active",
            f"{WORLD_ID}:instance",
        )
        seed_event(
            self.store,
            tenant_id,
            "usr_migrated",
            "migrated-leave",
            event_time + timedelta(minutes=3),
            "offline",
            "offline",
        )
        discovery, _, _ = self.service()

        self.assertEqual(discovery.discover(tenant_id, 7)["hot"], [])

        seed_tracking(
            self.store,
            tenant_id,
            "usr_migrated",
            event_time - timedelta(minutes=1),
        )
        seed_coverage_pair(self.store, tenant_id, event_time)
        migrated = discovery.discover(tenant_id, 7)

        self.assertEqual(
            [item["world_id"] for item in migrated["hot"]], [WORLD_ID]
        )
        self.assertEqual(migrated["hot"][0]["minutes"], 3.0)

    def test_pagination_ranks_all_worlds_but_hydrates_only_the_requested_page(self):
        tenant_id = self.create_tenant("paged-discovery")
        event_time = self.current - timedelta(days=1)
        for friend_id, world_id, minutes in (
            ("usr_a", WORLD_ID, 2),
            ("usr_b", WORLD_B, 4),
            ("usr_c", WORLD_C, 6),
        ):
            seed_friend(
                self.store,
                tenant_id,
                friend_id,
                friend_id,
                updated_at=event_time,
            )
            seed_tracking(
                self.store,
                tenant_id,
                friend_id,
                event_time - timedelta(minutes=1),
            )
            seed_event(
                self.store,
                tenant_id,
                friend_id,
                f"{friend_id}-enter",
                event_time,
                "active",
                f"{world_id}:instance",
            )
            seed_event(
                self.store,
                tenant_id,
                friend_id,
                f"{friend_id}-leave",
                event_time + timedelta(minutes=minutes),
                "offline",
                "offline",
            )
        seed_coverage_pair(self.store, tenant_id, event_time)
        discovery, _, _ = self.service()

        with mock.patch.object(
            self.store, "world_cache_get", wraps=self.store.world_cache_get
        ) as cache_get, mock.patch.object(
            discovery,
            "_world_presentation",
            side_effect=lambda _tenant_id, world_id: {
                "id": world_id,
                "name": world_id,
                "resolution_status": "ready",
                "stale": False,
            },
        ) as present:
            result = discovery.discover(tenant_id, 7, limit=1, offset=1)

        self.assertEqual(result["hot_total"], 3)
        self.assertEqual(result["rising_total"], 3)
        self.assertEqual(result["limit"], 1)
        self.assertEqual(result["offset"], 1)
        self.assertEqual([item["world_id"] for item in result["hot"]], [WORLD_B])
        self.assertEqual(
            [item["world_id"] for item in result["rising"]], [WORLD_B]
        )
        self.assertEqual(result["hot"][0]["rank"], 2)
        self.assertEqual(result["rising"][0]["rank"], 2)
        present.assert_called_once_with(tenant_id, WORLD_B)
        self.assertEqual(cache_get.call_count, 0)

    def test_one_day_and_all_scopes_use_the_earliest_relevant_observation(self):
        tenant_id = self.create_tenant("discovery-scopes")
        old_time = self.current - timedelta(days=40)
        recent_time = self.current - timedelta(hours=12)
        tracking_start = old_time - timedelta(minutes=1)
        seed_friend(
            self.store,
            tenant_id,
            "usr_scopes",
            "Scopes",
            updated_at=old_time,
        )
        seed_tracking(
            self.store, tenant_id, "usr_scopes", tracking_start
        )
        seed_coverage_pair(self.store, tenant_id, old_time)
        seed_coverage_pair(self.store, tenant_id, recent_time)
        for event_id, occurred_at, status, location in (
            ("old-enter", old_time, "active", f"{WORLD_ID}:old"),
            ("old-leave", old_time + timedelta(minutes=3), "offline", "offline"),
            ("recent-enter", recent_time, "active", f"{WORLD_B}:recent"),
            (
                "recent-leave",
                recent_time + timedelta(minutes=4),
                "offline",
                "offline",
            ),
        ):
            seed_event(
                self.store,
                tenant_id,
                "usr_scopes",
                event_id,
                occurred_at,
                status,
                location,
            )
        discovery, _, _ = self.service()

        one_day = discovery.discover(tenant_id, 1)
        all_time = discovery.discover(tenant_id, 0)

        self.assertEqual(
            [item["world_id"] for item in one_day["hot"]], [WORLD_B]
        )
        self.assertEqual(
            [item["world_id"] for item in all_time["hot"]],
            [WORLD_B, WORLD_ID],
        )
        self.assertEqual(all_time["range"]["days"], 0)
        self.assertEqual(all_time["range"]["from"], as_text(tracking_start))

    def test_private_hidden_and_gap_time_do_not_become_world_time(self):
        tenant_id = self.create_tenant("coverage")
        start = self.current - timedelta(days=1)
        seed_friend(
            self.store,
            tenant_id,
            "usr_states",
            "States",
            updated_at=start,
        )
        seed_friend(
            self.store,
            tenant_id,
            "usr_gap",
            "Gap",
            updated_at=start,
        )
        seed_tracking(self.store, tenant_id, "usr_states", start - timedelta(minutes=1))
        seed_tracking(self.store, tenant_id, "usr_gap", start - timedelta(minutes=1))
        seed_coverage_pair(self.store, tenant_id, start)
        seed_coverage_pair(self.store, tenant_id, start + timedelta(hours=1))

        seed_event(
            self.store,
            tenant_id,
            "usr_states",
            "states-world",
            start,
            "active",
            f"{WORLD_ID}:instance",
        )
        seed_event(
            self.store,
            tenant_id,
            "usr_states",
            "states-private",
            start + timedelta(minutes=2),
            "active",
            "private",
        )
        seed_event(
            self.store,
            tenant_id,
            "usr_states",
            "states-hidden",
            start + timedelta(minutes=4),
            "active",
            "online",
        )
        seed_event(
            self.store,
            tenant_id,
            "usr_states",
            "states-traveling",
            start + timedelta(minutes=6),
            "active",
            "traveling",
        )
        seed_event(
            self.store,
            tenant_id,
            "usr_states",
            "states-offline",
            start + timedelta(minutes=8),
            "offline",
            "offline",
        )
        seed_event(
            self.store,
            tenant_id,
            "usr_gap",
            "gap-world",
            start,
            "active",
            f"{WORLD_B}:instance",
        )
        seed_event(
            self.store,
            tenant_id,
            "usr_gap",
            "gap-offline",
            start + timedelta(minutes=90),
            "offline",
            "offline",
        )

        discovery, _, _ = self.service()
        result = discovery.discover(tenant_id, 7)
        worlds = {item["world_id"]: item for item in result["hot"]}

        self.assertEqual(set(worlds), {WORLD_ID, WORLD_B})
        self.assertEqual(worlds[WORLD_ID]["minutes"], 2.0)
        self.assertEqual(worlds[WORLD_B]["minutes"], 22.0)
        self.assertEqual(worlds[WORLD_B]["visit_count"], 1)
        self.assertEqual(result["unavailable_minutes"], 6.0)
        self.assertEqual(result["coverage"]["covered_minutes"], 22.0)

    def test_hot_and_rising_rankings_use_current_and_previous_ranges(self):
        tenant_id = self.create_tenant("rankings")
        previous_time = self.current - timedelta(days=10)
        current_time = self.current - timedelta(days=2)
        for friend_id, name in (
            ("usr_alice", "Alice"),
            ("usr_bob", "Bob"),
            ("usr_charlie", "Charlie"),
        ):
            seed_friend(
                self.store,
                tenant_id,
                friend_id,
                name,
                updated_at=previous_time,
            )
            seed_tracking(
                self.store,
                tenant_id,
                friend_id,
                previous_time - timedelta(hours=1),
            )
        seed_coverage_pair(self.store, tenant_id, previous_time)
        seed_coverage_pair(self.store, tenant_id, current_time)

        seed_event(
            self.store,
            tenant_id,
            "usr_alice",
            "previous-a-enter",
            previous_time,
            "active",
            f"{WORLD_ID}:previous",
        )
        seed_event(
            self.store,
            tenant_id,
            "usr_alice",
            "previous-a-leave",
            previous_time + timedelta(minutes=4),
            "offline",
            "offline",
        )
        seed_event(
            self.store,
            tenant_id,
            "usr_alice",
            "current-a-alice-enter",
            current_time,
            "active",
            f"{WORLD_ID}:current",
        )
        seed_event(
            self.store,
            tenant_id,
            "usr_alice",
            "current-a-alice-leave",
            current_time + timedelta(minutes=6),
            "offline",
            "offline",
        )
        seed_event(
            self.store,
            tenant_id,
            "usr_bob",
            "current-a-bob-enter",
            current_time,
            "active",
            f"{WORLD_ID}:current",
        )
        seed_event(
            self.store,
            tenant_id,
            "usr_bob",
            "current-a-bob-leave",
            current_time + timedelta(minutes=4),
            "offline",
            "offline",
        )
        seed_event(
            self.store,
            tenant_id,
            "usr_charlie",
            "current-b-enter",
            current_time,
            "active",
            f"{WORLD_B}:current",
        )
        seed_event(
            self.store,
            tenant_id,
            "usr_charlie",
            "current-b-leave",
            current_time + timedelta(minutes=10),
            "offline",
            "offline",
        )

        discovery, fetcher, _ = self.service()
        with mock.patch.object(
            discovery, "_build_response", wraps=discovery._build_response
        ) as build, mock.patch.object(
            discovery, "_snapshot", wraps=discovery._snapshot
        ) as snapshot:
            first = discovery.discover(tenant_id, 7)
            second = discovery.discover(tenant_id, 7)

        self.assertEqual(build.call_count, 1)
        self.assertEqual(snapshot.call_count, 1)
        self.assertEqual(first, second)
        self.assertEqual(fetcher.calls, [])
        self.assertEqual(
            [item["world_id"] for item in first["hot"]],
            [WORLD_ID, WORLD_B],
        )
        world_a = next(
            item for item in first["rising"] if item["world_id"] == WORLD_ID
        )
        self.assertEqual(world_a["current"]["minutes"], 10.0)
        self.assertEqual(world_a["current"]["unique_people"], 2)
        self.assertEqual(world_a["current"]["visit_count"], 2)
        self.assertEqual(world_a["previous"]["minutes"], 4.0)
        self.assertEqual(world_a["previous"]["unique_people"], 1)
        self.assertEqual(world_a["previous"]["visit_count"], 1)
        self.assertEqual(
            world_a["delta"],
            {"minutes": 6.0, "unique_people": 1, "visit_count": 1},
        )
        self.assertEqual(first["range"]["days"], 7)
        self.assertEqual(
            first["ranking"]["hot"][0], "unique_people:desc"
        )

    def test_friend_and_world_tag_filters_are_scoped_and_self_can_be_excluded(self):
        tenant_id = self.create_tenant("filters")
        event_time = self.current - timedelta(days=1)
        seed_friend(
            self.store,
            tenant_id,
            "usr_self",
            "Owner",
            is_self=True,
            updated_at=event_time,
        )
        seed_friend(
            self.store,
            tenant_id,
            "usr_friend",
            "Friend",
            updated_at=event_time,
        )
        for friend_id in ("usr_self", "usr_friend"):
            seed_tracking(
                self.store,
                tenant_id,
                friend_id,
                event_time - timedelta(minutes=1),
            )
        seed_coverage_pair(self.store, tenant_id, event_time)
        seed_event(
            self.store,
            tenant_id,
            "usr_self",
            "self-enter",
            event_time,
            "active",
            f"{WORLD_C}:self",
        )
        seed_event(
            self.store,
            tenant_id,
            "usr_self",
            "self-leave",
            event_time + timedelta(minutes=3),
            "offline",
            "offline",
        )
        seed_event(
            self.store,
            tenant_id,
            "usr_friend",
            "friend-enter",
            event_time,
            "active",
            f"{WORLD_B}:friend",
        )
        seed_event(
            self.store,
            tenant_id,
            "usr_friend",
            "friend-leave",
            event_time + timedelta(minutes=4),
            "offline",
            "offline",
        )
        self.store.world_cache_put(
            WORLD_B,
            {
                "id": WORLD_B,
                "name": "Social World",
                "tags": ["author_tag_social", "system_approved"],
            },
        )
        self.store.world_cache_put(
            WORLD_C,
            {
                "id": WORLD_C,
                "name": "Game World",
                "tags": ["author_tag_game", "system_approved"],
            },
        )

        other_tenant = self.create_tenant("other")
        seed_friend(
            self.store,
            other_tenant,
            "usr_other",
            "Other",
            updated_at=event_time,
        )
        discovery, fetcher, resolver = self.service()
        without_self = discovery.discover(tenant_id, 7, include_self=False)
        tagged = discovery.discover(
            tenant_id, 7, world_tag="author_tag_social"
        )
        only_self = discovery.discover(tenant_id, 7, friend_id="usr_self")

        self.assertEqual(
            [item["world_id"] for item in without_self["hot"]], [WORLD_B]
        )
        self.assertEqual(
            [item["world_id"] for item in tagged["hot"]], [WORLD_B]
        )
        self.assertEqual(
            [item["world_id"] for item in only_self["hot"]], [WORLD_C]
        )
        with self.assertRaises(KeyError):
            discovery.discover(tenant_id, 7, friend_id="usr_other")

        library = WorldService(self.store, resolver)
        with self.assertRaises(KeyError):
            library.library(tenant_id, friend_id="usr_other")
        tagged_library = library.library(
            tenant_id, world_tag="author_tag_social"
        )
        self.assertEqual(
            [item["id"] for item in tagged_library["items"]], [WORLD_B]
        )
        self.assertEqual(
            library.tags(tenant_id),
            [
                {"name": "system_approved", "count": 2},
                {"name": "author_tag_game", "count": 1},
                {"name": "author_tag_social", "count": 1},
            ],
        )
        self.assertEqual(fetcher.calls, [])

    def test_discovery_accepts_only_product_ranges(self):
        tenant_id = self.create_tenant("ranges")
        discovery, _, _ = self.service()
        for days in (0, 1, 7, 30):
            with self.subTest(days=days):
                self.assertEqual(discovery.discover(tenant_id, days)["range"]["days"], days)
        with self.assertRaises(ValueError):
            discovery.discover(tenant_id, 14)


if __name__ == "__main__":
    unittest.main()
