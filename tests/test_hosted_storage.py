from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from server.storage import Store


class HostedStorageContractTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(f"{self.directory.name}/hosted.sqlite3")
        self.tenant = self.store.bootstrap("Alice", "bridge")
        self.collector = self.store.auth(self.tenant["collector_token"], "collector")

    def tearDown(self):
        self.directory.cleanup()

    def ingest(self, friend_id: str, name: str, status: str = "active") -> None:
        assert self.collector is not None
        self.store.ingest(
            self.collector["tenant_id"],
            self.collector["id"],
            [{"id": friend_id, "displayName": name, "status": status}],
            [],
        )

    def test_connections_enable_foreign_keys_and_readiness_checks_database(self):
        with self.store.connection() as db:
            enabled = db.execute("PRAGMA foreign_keys").fetchone()[0]
        self.assertEqual(enabled, 1)
        self.assertTrue(self.store.ready())

    def test_overview_totals_are_not_derived_from_a_truncated_event_page(self):
        self.ingest("usr_1", "Alice", "active")
        assert self.collector is not None
        current = datetime.now(timezone.utc)
        recent_events = [
            {
                "client_event_id": f"event-{index}",
                "friend_id": "usr_1",
                "occurred_at": (current - timedelta(hours=index)).isoformat(),
                "old_status": "offline",
                "new_status": "active",
            }
            for index in range(12)
        ]
        old_event = {
            "client_event_id": "event-old",
            "friend_id": "usr_1",
            "occurred_at": (current - timedelta(days=8)).isoformat(),
            "old_status": "active",
            "new_status": "offline",
        }
        self.store.ingest(self.collector["tenant_id"], self.collector["id"], [], [*recent_events, old_event])

        page = self.store.events_page(self.collector["tenant_id"], limit=5)
        overview = self.store.overview(self.collector["tenant_id"])

        self.assertEqual(len(page["items"]), 5)
        self.assertEqual(page["total"], 13)
        self.assertEqual(overview["event_total"], 13)
        self.assertEqual(overview["change_count_7d"], 12)
        self.assertEqual(overview["tracked_count"], 1)
        self.assertEqual(overview["online_count"], 1)
        self.assertEqual(overview["collector_state"], "fresh")
        self.assertIsInstance(overview["sync_age_seconds"], int)

    def test_overview_reports_never_stale_and_collector_error_states(self):
        empty = self.store.bootstrap("Empty", "bridge")
        empty_collector = self.store.auth(empty["collector_token"], "collector")
        assert empty_collector is not None
        never = self.store.overview(empty_collector["tenant_id"], stale_after_seconds=60)
        self.assertEqual(never["collector_state"], "never")

        self.ingest("usr_1", "Alice", "active")
        assert self.collector is not None
        with self.store.connection() as db:
            db.execute(
                "UPDATE collectors SET last_sync=? WHERE id=?",
                ("2000-01-01T00:00:00+00:00", self.collector["id"]),
            )
        stale = self.store.overview(self.collector["tenant_id"], stale_after_seconds=60)
        self.assertEqual(stale["collector_state"], "stale")

        self.store.mark_collector_error(self.collector["id"], "bridge unavailable")
        failed = self.store.overview(self.collector["tenant_id"], stale_after_seconds=60)
        self.assertEqual(failed["collector_state"], "error")

    def test_paginated_people_and_events_are_searchable_and_tenant_scoped(self):
        self.ingest("usr_1", "Alice", "active")
        self.ingest("usr_2", "Bob", "offline")
        other = self.store.bootstrap("Other", "bridge")
        other_collector = self.store.auth(other["collector_token"], "collector")
        assert other_collector is not None
        self.store.ingest(
            other_collector["tenant_id"],
            other_collector["id"],
            [{"id": "usr_private", "displayName": "Private tenant", "status": "active"}],
            [],
        )

        assert self.collector is not None
        people = self.store.friends_page(self.collector["tenant_id"], query="ali", limit=20)
        offline = self.store.friends_page(self.collector["tenant_id"], status="offline", limit=20)
        online = self.store.friends_page(self.collector["tenant_id"], status="online", limit=20)

        self.assertEqual([item["display_name"] for item in people["items"]], ["Alice"])
        self.assertEqual([item["display_name"] for item in offline["items"]], ["Bob"])
        self.assertEqual([item["display_name"] for item in online["items"]], ["Alice"])
        self.assertNotIn("Private tenant", repr(people) + repr(offline))

    def test_expired_browser_sessions_can_be_removed_without_touching_active_sessions(self):
        expired = self.store.exchange_access_code(self.tenant["access_code"])
        active = self.store.exchange_access_code(self.tenant["access_code"])
        assert expired is not None and active is not None
        with self.store.connection() as db:
            db.execute(
                "UPDATE viewer_tokens SET expires_at=? WHERE token_hash=?",
                ("2000-01-01T00:00:00+00:00", self.store.hash_token(expired["session_token"])),
            )

        removed = self.store.cleanup_expired_sessions()

        self.assertEqual(removed, 1)
        self.assertIsNone(self.store.auth(expired["session_token"], "viewer"))
        self.assertIsNotNone(self.store.auth(active["session_token"], "viewer"))


if __name__ == "__main__":
    unittest.main()
