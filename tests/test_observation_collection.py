from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from server.schemas import TelemetryRequest
from server.storage import Store


def friend(
    friend_id: str,
    display_name: str,
    status: str = "active",
    *,
    observed_at: str,
) -> dict[str, object]:
    return {
        "id": friend_id,
        "username": display_name.lower(),
        "display_name": display_name,
        "status": status,
        "location": "offline" if status == "offline" else "wrld_example:1",
        "platform": "standalonewindows",
        "updated_at": observed_at,
    }


class ObservationCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(str(Path(self.directory.name) / "hosted.sqlite3"))
        seeded = self.store.bootstrap("Alice", "collector")
        self.tenant_id = seeded["tenant_id"]
        self.collector_id = seeded["collector_id"]

    def ingest(
        self,
        observed_at: str,
        friends: list[dict[str, object]],
        events: list[dict[str, object]] | None = None,
    ) -> dict[str, int]:
        return self.store.ingest_authoritative_snapshot(
            self.tenant_id,
            self.collector_id,
            friends,
            events or [],
            source="hosted-rest",
            observed_at=observed_at,
            expected_interval_seconds=180,
            duration_ms=120,
        )

    def test_authoritative_snapshot_records_samples_and_tracking_edges(self):
        first = "2026-08-30T08:00:00+00:00"
        second = "2026-08-30T08:03:00+00:00"
        self.ingest(first, [friend("usr_a", "Alice", observed_at=first)])
        self.ingest(
            second,
            [friend("usr_b", "Bob", "offline", observed_at=second)],
        )

        self.assertEqual(self.store.collection_sample_count(self.tenant_id), 2)
        self.assertEqual(
            [
                (item["friend_id"], bool(item["tracked"]))
                for item in self.store.tracking_events(self.tenant_id)
            ],
            [("usr_a", True), ("usr_a", False), ("usr_b", True)],
        )

    def test_same_identity_change_is_idempotent(self):
        first = "2026-08-30T08:00:00+00:00"
        second = "2026-08-30T08:03:00+00:00"
        third = "2026-08-30T08:06:00+00:00"
        self.ingest(first, [friend("usr_a", "Alice", observed_at=first)])
        renamed = friend("usr_a", "Alicia", observed_at=second)
        renamed["username"] = "alice"
        unchanged = friend("usr_a", "Alicia", observed_at=third)
        unchanged["username"] = "alice"
        self.ingest(second, [renamed])
        self.ingest(third, [unchanged])

        self.assertEqual(self.store.identity_event_count(self.tenant_id), 1)

    def test_repeated_failure_records_only_the_failure_edge(self):
        self.store.record_collection_failure(
            self.tenant_id,
            "hosted-rest",
            "network",
            180,
            observed_at="2026-08-30T08:00:00+00:00",
        )
        self.store.record_collection_failure(
            self.tenant_id,
            "hosted-rest",
            "network",
            180,
            observed_at="2026-08-30T08:03:00+00:00",
        )

        self.assertEqual(self.store.collection_failure_count(self.tenant_id), 1)

    def test_future_event_is_preserved_but_quarantined(self):
        stamp = "2026-08-30T08:00:00+00:00"
        event = {
            "client_event_id": "future-event",
            "friend_id": "usr_a",
            "occurred_at": "2026-08-30T09:00:00+00:00",
            "old_status": "offline",
            "new_status": "active",
            "location": "wrld_future:1",
            "source": "hosted-rest",
        }
        self.ingest(stamp, [friend("usr_a", "Alice", observed_at=stamp)], [event])

        with self.store.connection() as db:
            stored = db.execute(
                "SELECT 1 FROM status_events WHERE tenant_id=? AND client_event_id=?",
                (self.tenant_id, "future-event"),
            ).fetchone()
            anomaly = db.execute(
                """SELECT reason FROM event_anomalies
                WHERE tenant_id=? AND event_id=?""",
                (self.tenant_id, "future-event"),
            ).fetchone()
        self.assertIsNotNone(stored)
        self.assertEqual(str(anomaly[0]), "future_timestamp")

    def test_invalid_snapshot_rolls_back_evidence_and_friend_changes(self):
        stamp = "2026-08-30T08:00:00+00:00"
        invalid_event = {
            "client_event_id": "missing-friend",
            "friend_id": "usr_missing",
            "occurred_at": stamp,
        }
        with self.assertRaises(ValueError):
            self.ingest(
                stamp,
                [friend("usr_a", "Alice", observed_at=stamp)],
                [invalid_event],
            )

        self.assertEqual(self.store.collection_sample_count(self.tenant_id), 0)
        self.assertEqual(self.store.tracking_events(self.tenant_id), [])
        self.assertEqual(self.store.friend_states(self.tenant_id), {})

    def test_telemetry_v2_requires_explicit_authoritative_observation(self):
        with self.assertRaises(ValidationError):
            TelemetryRequest.model_validate(
                {"schema_version": 2, "friends": [], "events": []}
            )
        with self.assertRaises(ValidationError):
            TelemetryRequest.model_validate(
                {
                    "schema_version": 1,
                    "friends": [],
                    "events": [],
                    "observation": {
                        "observed_at": "2026-08-30T08:00:00+00:00",
                        "expected_interval_seconds": 180,
                        "authoritative": True,
                    },
                }
            )
        payload = TelemetryRequest.model_validate(
            {
                "schema_version": 2,
                "friends": [],
                "events": [],
                "observation": {
                    "observed_at": "2026-08-30T08:00:00+00:00",
                    "expected_interval_seconds": 180,
                    "authoritative": True,
                },
            }
        )
        self.assertEqual(payload.schema_version, 2)


if __name__ == "__main__":
    unittest.main()
