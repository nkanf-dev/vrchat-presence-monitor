from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import create_app
from server.settings import Settings
from server.storage import Store, now


class HostedHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        static = Path(self.directory.name) / "static"
        static.mkdir()
        (static / "index.html").write_text("<div id='root'></div>", encoding="utf-8")
        self.settings = Settings(
            data_dir=Path(self.directory.name),
            static_dir=static,
            bootstrap_token="bootstrap-secret",
            cookie_secure="never",
            trust_proxy_headers=False,
        )
        self.store = Store(str(self.settings.data_dir / "hosted.sqlite3"))
        self.client = TestClient(create_app(self.settings, self.store))
        self.addCleanup(self.client.close)

    def test_health_requires_admin_token_and_returns_compact_categories(self) -> None:
        tenant = self.store.bootstrap("Alice", "collector")
        self.store.record_collection_failure(
            tenant["tenant_id"],
            "hosted-rest",
            "network",
            180,
        )

        self.assertEqual(self.client.get("/v1/admin/health").status_code, 403)
        response = self.client.get(
            "/v1/admin/health",
            headers={"x-bootstrap-token": "bootstrap-secret"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()["tenants"][0]
        self.assertEqual(item["category"], "site_network")
        self.assertEqual(item["state"], "degraded")
        self.assertEqual(item["success_rate"], 0.0)
        self.assertNotIn("error", item)
        self.assertNotIn("cookie", response.text.lower())

    def test_success_after_failure_clears_active_failure_category(self) -> None:
        tenant = self.store.bootstrap("Alice", "collector")
        tenant_id = tenant["tenant_id"]
        collector_id = tenant["collector_id"]
        self.store.record_collection_failure(
            tenant_id,
            "hosted-rest",
            "network",
            180,
        )
        self.store.ingest_authoritative_snapshot(
            tenant_id,
            collector_id,
            [{"id": "usr_alice", "display_name": "Alice", "status": "offline"}],
            [],
            source="hosted-rest",
            observed_at=now(),
            expected_interval_seconds=180,
        )

        item = self.client.get(
            "/v1/admin/health",
            headers={"x-bootstrap-token": "bootstrap-secret"},
        ).json()["tenants"][0]

        self.assertIsNone(item["category"])
        self.assertEqual(item["state"], "healthy")
        self.assertEqual(item["success_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
