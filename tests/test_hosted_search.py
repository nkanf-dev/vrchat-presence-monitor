from __future__ import annotations

import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import create_app
from server.settings import Settings
from server.storage import Store, stable_id


class HostedSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        static = Path(self.directory.name) / "static"
        static.mkdir()
        (static / "index.html").write_text("<div id='root'></div>", encoding="utf-8")
        settings = Settings(
            data_dir=Path(self.directory.name),
            static_dir=static,
            bootstrap_token="bootstrap-secret",
            cookie_secure="never",
            trust_proxy_headers=False,
        )
        self.store = Store(str(settings.data_dir / "hosted.sqlite3"))
        self.client = TestClient(create_app(settings, self.store))
        self.addCleanup(self.client.close)
        created = self.store.bootstrap("Alice", "collector")
        self.tenant_id = created["tenant_id"]
        self.collector_id = created["collector_id"]
        self.assertEqual(
            self.client.post(
                "/v1/login",
                headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
                json={"access_code": created["access_code"]},
            ).status_code,
            200,
        )
        stamp = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        self.store.ingest(
            self.tenant_id,
            self.collector_id,
            [
                {
                    "id": "usr_alice",
                    "username": "alicia",
                    "display_name": "Alicia",
                    "status": "active",
                    "location": "wrld_00000000-0000-0000-0000-000000000123:1",
                    "updated_at": stamp,
                }
            ],
            [
                {
                    "client_event_id": "event-search",
                    "friend_id": "usr_alice",
                    "occurred_at": stamp,
                    "old_status": "offline",
                    "new_status": "active",
                    "location": "wrld_00000000-0000-0000-0000-000000000123:1",
                    "source": "hosted-rest",
                }
            ],
        )
        with self.store.connection() as db:
            db.execute(
                """INSERT INTO friend_identity_events(
                    tenant_id,event_id,friend_id,field,old_value,new_value,occurred_at,source
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    self.tenant_id,
                    stable_id("identity", "usr_alice", "display_name", "Alice", "Alicia"),
                    "usr_alice",
                    "display_name",
                    "Alice",
                    "Alicia",
                    stamp,
                    "hosted-rest",
                ),
            )
            db.execute(
                """INSERT INTO friend_annotations(
                    tenant_id,friend_id,note,pinned,revision,updated_at
                ) VALUES(?,?,?,?,?,?)""",
                (self.tenant_id, "usr_alice", "Tea friend", 1, "rev-a", stamp),
            )
        self.store.world_cache_put(
            "wrld_00000000-0000-0000-0000-000000000123",
            {
                "id": "wrld_00000000-0000-0000-0000-000000000123",
                "name": "Alice's Tea Room",
                "author_name": "Builder",
                "thumbnail_url": "https://api.vrchat.cloud/api/1/file/example",
            },
        )

    def test_search_groups_people_names_notes_worlds_and_history(self):
        response = self.client.get("/v1/search", params={"q": "alice", "limit": 8})
        self.assertEqual(response.status_code, 200, response.text)
        groups = response.json()["groups"]
        self.assertEqual(
            set(groups), {"people", "worlds", "history", "destinations"}
        )
        person = groups["people"][0]
        self.assertEqual(person["id"], "usr_alice")
        self.assertIn("historical_name", person["matches"])
        self.assertEqual(groups["worlds"][0]["name"], "Alice's Tea Room")

    def test_search_recognizes_an_observed_vrchat_world_url(self):
        world_id = "wrld_00000000-0000-0000-0000-000000000123"
        response = self.client.get(
            "/v1/search",
            params={"q": f"https://vrchat.com/home/world/{world_id}", "limit": 8},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["groups"]["worlds"][0]["id"], world_id)

    def test_history_search_result_uses_canonical_history_href(self):
        response = self.client.get(
            "/v1/search",
            params={"q": "active", "limit": 8},
        )
        self.assertEqual(response.status_code, 200, response.text)
        history = response.json()["groups"]["history"]
        self.assertTrue(history)
        self.assertEqual(
            {item["href"] for item in history},
            {"#area=more&section=history&historyQ=active"},
        )

    def test_search_never_returns_other_tenant_note(self):
        other = self.store.bootstrap("Other", "collector")
        self.store.ingest(
            other["tenant_id"],
            other["collector_id"],
            [{"id": "usr_other", "display_name": "Other"}],
            [],
        )
        stamp = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        with self.store.connection() as db:
            db.execute(
                """INSERT INTO friend_annotations(
                    tenant_id,friend_id,note,pinned,revision,updated_at
                ) VALUES(?,?,?,?,?,?)""",
                (other["tenant_id"], "usr_other", "secret-other-tenant", 0, "rev", stamp),
            )
        encoded = json.dumps(
            self.client.get("/v1/search", params={"q": "secret"}).json()
        )
        self.assertNotIn("secret-other-tenant", encoded)

    def test_search_is_bounded_and_fast_on_compact_entities(self):
        started = time.perf_counter()
        response = self.client.get("/v1/search", params={"q": "alice", "limit": 1})
        elapsed = time.perf_counter() - started
        self.assertEqual(response.status_code, 200, response.text)
        self.assertLess(elapsed, 0.3)
        for items in response.json()["groups"].values():
            self.assertLessEqual(len(items), 1)


if __name__ == "__main__":
    unittest.main()
