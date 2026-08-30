from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from server.app import create_app
from server.settings import Settings
from server.storage import Store


ZONE = ZoneInfo("Asia/Shanghai")


class HostedAnalyticsTests(unittest.TestCase):
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
        response = self.client.post(
            "/v1/login",
            headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
            json={"access_code": created["access_code"]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.day = datetime.now(ZONE).date() - timedelta(days=1)

    def stamp(self, hour: int, minute: int = 0) -> str:
        return datetime.combine(
            self.day, time(hour, minute), tzinfo=ZONE
        ).astimezone(timezone.utc).isoformat(timespec="microseconds")

    def seed_observed_session(self) -> None:
        for minute in range(0, 31, 3):
            observed_at = self.stamp(8, minute)
            online = minute < 30
            friend = {
                "id": "usr_alice",
                "username": "alice",
                "display_name": "Alice",
                "status": "active" if online else "offline",
                "location": "wrld_00000000-0000-0000-0000-000000000001:1"
                if online
                else "offline",
                "platform": "standalonewindows",
                "updated_at": observed_at,
            }
            events = []
            if minute in {0, 30}:
                events.append(
                    {
                        "client_event_id": f"event-{minute}",
                        "friend_id": "usr_alice",
                        "occurred_at": observed_at,
                        "old_status": "offline" if minute == 0 else "active",
                        "new_status": friend["status"],
                        "location": friend["location"],
                        "platform": friend["platform"],
                        "source": "hosted-rest",
                    }
                )
            self.store.ingest_authoritative_snapshot(
                self.tenant_id,
                self.collector_id,
                [friend],
                events,
                source="hosted-rest",
                observed_at=observed_at,
                expected_interval_seconds=180,
                duration_ms=25,
            )

    def test_presence_heatmap_exposes_evidence_per_person_hour(self):
        self.seed_observed_session()
        selected = self.day.isoformat()
        response = self.client.get(
            "/v1/analytics/presence",
            params={
                "day": selected,
                "heatmap_from": selected,
                "heatmap_to": selected,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        row = next(item for item in payload["heatmap"] if item["id"] == "usr_alice")
        cell = row["cells"][8]
        self.assertEqual(
            set(cell),
            {
                "ratio",
                "online_minutes",
                "observed_minutes",
                "eligible_minutes",
                "covered_days",
                "range_days",
            },
        )
        self.assertAlmostEqual(cell["ratio"], 0.75)
        self.assertEqual(cell["online_minutes"], 30)
        self.assertEqual(cell["observed_minutes"], 40)
        self.assertEqual(cell["covered_days"], 1)
        self.assertEqual(payload["coverage"]["observed_minutes"], 40)

    def test_world_timeline_reports_location_category_and_coverage(self):
        self.seed_observed_session()
        response = self.client.get(
            "/v1/analytics/worlds", params={"day": self.day.isoformat()}
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        row = next(item for item in payload["friends"] if item["id"] == "usr_alice")
        self.assertEqual(row["spans"][0]["location_kind"], "world")
        self.assertEqual(payload["coverage"]["observed_minutes"], 40)
        self.assertTrue(payload["gaps"])

    def test_standalone_coverage_reports_gaps_and_timezone(self):
        self.seed_observed_session()
        selected = self.day.isoformat()
        response = self.client.get(
            "/v1/coverage", params={"from": selected, "to": selected}
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["timezone"], "Asia/Shanghai")
        self.assertEqual(payload["observed_minutes"], 40)
        self.assertGreaterEqual(len(payload["gaps"]), 1)

    def test_stale_current_state_is_not_presented_as_live(self):
        stale = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(
            timespec="microseconds"
        )
        with self.store.connection() as db:
            db.execute(
                "UPDATE collectors SET last_sync=?,last_error='' WHERE id=?",
                (stale, self.collector_id),
            )

        response = self.client.get("/v1/overview")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["collector_state"], "stale")
        self.assertFalse(response.json()["live"])


if __name__ == "__main__":
    unittest.main()
