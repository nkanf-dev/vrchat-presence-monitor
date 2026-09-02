from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import create_app
from server.settings import Settings
from server.storage import Store


class HostedOrganizationTests(unittest.TestCase):
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
        self.access_code = created["access_code"]
        self.tenant_id = created["tenant_id"]
        self.collector_id = created["collector_id"]
        self.assertEqual(
            self.client.post(
                "/v1/login",
                headers=self.mutation_headers(),
                json={"access_code": created["access_code"]},
            ).status_code,
            200,
        )
        self.store.ingest(
            self.tenant_id,
            self.collector_id,
            [
                {
                    "id": "usr_alice",
                    "username": "alice",
                    "display_name": "Alice",
                    "status": "offline",
                    "location": "offline",
                }
            ],
            [],
        )

    @staticmethod
    def mutation_headers() -> dict[str, str]:
        return {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}

    def test_annotation_revision_conflict_returns_current_value(self):
        first = self.client.put(
            "/v1/friends/usr_alice/annotation",
            headers=self.mutation_headers(),
            json={"note": "第一次", "pinned": True, "revision": None},
        )
        self.assertEqual(first.status_code, 200, first.text)
        conflict = self.client.put(
            "/v1/friends/usr_alice/annotation",
            headers=self.mutation_headers(),
            json={"note": "另一台设备", "pinned": False, "revision": "stale"},
        )
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(conflict.json()["server"]["note"], "第一次")
        self.assertEqual(
            self.client.get("/v1/friends/usr_alice/annotation").json()["revision"],
            first.json()["revision"],
        )

    def test_tags_are_case_insensitively_unique_and_assignable(self):
        created = self.client.post(
            "/v1/tags",
            headers=self.mutation_headers(),
            json={"name": "常玩", "color": "#8bd450"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        duplicate = self.client.post(
            "/v1/tags",
            headers=self.mutation_headers(),
            json={"name": "常玩", "color": "#ffffff"},
        )
        self.assertEqual(duplicate.status_code, 409, duplicate.text)
        tag_id = created.json()["id"]
        assigned = self.client.put(
            f"/v1/friends/usr_alice/tags/{tag_id}",
            headers=self.mutation_headers(),
            json={},
        )
        self.assertEqual(assigned.status_code, 200, assigned.text)
        annotation = self.client.get("/v1/friends/usr_alice/annotation").json()
        self.assertEqual(annotation["tags"][0]["id"], tag_id)

    def test_timezone_requires_an_iana_name(self):
        invalid = self.client.put(
            "/v1/preferences",
            headers=self.mutation_headers(),
            json={"timezone": "GMT+8"},
        )
        self.assertEqual(invalid.status_code, 422, invalid.text)
        updated = self.client.put(
            "/v1/preferences",
            headers=self.mutation_headers(),
            json={"timezone": "America/New_York"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(
            self.client.get("/v1/preferences").json()["timezone"],
            "America/New_York",
        )

    def test_unknown_or_cross_tenant_friend_is_not_disclosed(self):
        other = self.store.bootstrap("Other", "collector")
        self.store.ingest(
            other["tenant_id"],
            other["collector_id"],
            [{"id": "usr_other", "display_name": "Other"}],
            [],
        )
        response = self.client.put(
            "/v1/friends/usr_other/annotation",
            headers=self.mutation_headers(),
            json={"note": "不能看到", "pinned": False, "revision": None},
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_dashboard_save_conflict_and_tenant_isolation(self):
        initial = self.client.get("/v1/dashboard")
        self.assertEqual(initial.status_code, 200, initial.text)
        self.assertIsNone(initial.json()["revision"])
        document = initial.json()["document"]
        document["title"] = "Alice 的仪表盘"
        saved = self.client.put(
            "/v1/dashboard",
            headers=self.mutation_headers(),
            json={"revision": None, "document": document},
        )
        self.assertEqual(saved.status_code, 200, saved.text)

        conflict = self.client.put(
            "/v1/dashboard",
            headers=self.mutation_headers(),
            json={"revision": "stale", "document": {**document, "title": "旧草稿"}},
        )
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(conflict.json()["server"]["document"]["title"], "Alice 的仪表盘")

        other = self.store.bootstrap("Other", "collector")
        self.assertEqual(
            self.client.post(
                "/v1/login",
                headers=self.mutation_headers(),
                json={"access_code": other["access_code"]},
            ).status_code,
            200,
        )
        other_dashboard = self.client.get("/v1/dashboard")
        self.assertEqual(other_dashboard.status_code, 200, other_dashboard.text)
        self.assertEqual(other_dashboard.json()["document"]["title"], "我的仪表盘")
        self.assertIsNone(other_dashboard.json()["revision"])

    def test_dashboard_rejects_invalid_grid_and_duplicate_ids(self):
        document = self.client.get("/v1/dashboard").json()["document"]
        document["panels"][0]["x"] = 11
        document["panels"][0]["w"] = 3
        invalid_grid = self.client.put(
            "/v1/dashboard",
            headers=self.mutation_headers(),
            json={"revision": None, "document": document},
        )
        self.assertEqual(invalid_grid.status_code, 422, invalid_grid.text)

        document = self.client.get("/v1/dashboard").json()["document"]
        document["panels"][1]["id"] = document["panels"][0]["id"]
        duplicate = self.client.put(
            "/v1/dashboard",
            headers=self.mutation_headers(),
            json={"revision": None, "document": document},
        )
        self.assertEqual(duplicate.status_code, 422, duplicate.text)

    def test_dashboard_share_password_and_access_audit(self):
        published = self.client.put(
            "/v1/dashboard/share",
            headers=self.mutation_headers(),
            json={"password": "friends-only"},
        )
        self.assertEqual(published.status_code, 200, published.text)
        share_id = published.json()["id"]

        locked = self.client.get(f"/v1/public/dashboard/{share_id}")
        self.assertEqual(locked.status_code, 200, locked.text)
        self.assertTrue(locked.json()["locked"])
        rejected = self.client.post(
            f"/v1/public/dashboard/{share_id}/unlock",
            headers=self.mutation_headers(),
            json={"password": "wrong"},
        )
        self.assertEqual(rejected.status_code, 401, rejected.text)
        unlocked = self.client.post(
            f"/v1/public/dashboard/{share_id}/unlock",
            headers=self.mutation_headers(),
            json={"password": "friends-only"},
        )
        self.assertEqual(unlocked.status_code, 200, unlocked.text)
        shared = self.client.get(f"/v1/public/dashboard/{share_id}")
        self.assertEqual(shared.status_code, 200, shared.text)
        self.assertFalse(shared.json()["locked"])
        audit = self.client.get("/v1/dashboard/share/audit").json()
        self.assertGreaterEqual(audit["total"], 3)
        self.assertTrue(all("password" not in item for item in audit["items"]))


if __name__ == "__main__":
    unittest.main()
