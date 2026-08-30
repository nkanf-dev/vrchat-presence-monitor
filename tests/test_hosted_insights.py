from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from server.insights import InsightsService, same_instance
from server.storage import Store


ZONE = ZoneInfo("Asia/Shanghai")
WORLD_A = "wrld_00000000-0000-0000-0000-000000000001:instance-a"
WORLD_B = "wrld_00000000-0000-0000-0000-000000000002:instance-b"


class HostedInsightsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(str(Path(self.directory.name) / "hosted.sqlite3"))
        created = self.store.bootstrap("Alice", "collector")
        self.tenant_id = created["tenant_id"]
        self.collector_id = created["collector_id"]
        self.day = date(2026, 8, 29)
        self._seed()
        self.insights = InsightsService(self.store)

    def stamp(self, hour: int, minute: int) -> str:
        return datetime.combine(
            self.day, time(hour, minute), tzinfo=ZONE
        ).astimezone(timezone.utc).isoformat(timespec="microseconds")

    def _seed(self) -> None:
        sample_minutes = [*range(0, 120, 9), 120]
        transitions = {
            0: [
                ("usr_self", "offline", "active", WORLD_A),
                ("usr_friend", "offline", "offline", "offline"),
            ],
            36: [("usr_friend", "offline", "active", WORLD_B, 30)],
            63: [("usr_friend", "active", "active", WORLD_A, 60)],
            81: [("usr_friend", "active", "active", WORLD_B, 80)],
            120: [("usr_friend", "active", "offline", "offline", 120)],
        }
        for elapsed in sample_minutes:
            hour = 8 + elapsed // 60
            minute = elapsed % 60
            observed_at = self.stamp(hour, minute)
            if elapsed < 30:
                friend_status, friend_location = "offline", "offline"
            elif elapsed < 60:
                friend_status, friend_location = "active", WORLD_B
            elif elapsed < 80:
                friend_status, friend_location = "active", WORLD_A
            elif elapsed < 120:
                friend_status, friend_location = "active", WORLD_B
            else:
                friend_status, friend_location = "offline", "offline"
            friends = [
                {
                    "id": "usr_self",
                    "username": "self",
                    "display_name": "Self",
                    "is_self": True,
                    "status": "active",
                    "location": WORLD_A,
                    "updated_at": observed_at,
                },
                {
                    "id": "usr_friend",
                    "username": "friend",
                    "display_name": "Friend",
                    "status": friend_status,
                    "location": friend_location,
                    "updated_at": observed_at,
                },
            ]
            events = []
            for transition in transitions.get(elapsed, []):
                friend_id, old_status, new_status, location, *actual = transition
                event_elapsed = actual[0] if actual else elapsed
                event_hour = 8 + event_elapsed // 60
                event_minute = event_elapsed % 60
                events.append(
                    {
                        "client_event_id": f"{friend_id}-{event_elapsed}",
                        "friend_id": friend_id,
                        "occurred_at": self.stamp(event_hour, event_minute),
                        "old_status": old_status,
                        "new_status": new_status,
                        "location": location,
                        "source": "hosted-rest",
                    }
                )
            self.store.ingest_authoritative_snapshot(
                self.tenant_id,
                self.collector_id,
                friends,
                events,
                source="hosted-rest",
                observed_at=observed_at,
                expected_interval_seconds=270,
            )

    def test_overlap_and_same_instance_are_distinct(self):
        result = self.insights.friend(
            self.tenant_id,
            "usr_friend",
            self.day.isoformat(),
            self.day.isoformat(),
        )
        self.assertEqual(result["online_overlap_minutes"], 90)
        self.assertEqual(result["co_presence_minutes"], 20)
        self.assertEqual(result["online_minutes"], 90)

    def test_private_hidden_or_world_only_match_is_not_same_instance(self):
        self.assertFalse(same_instance("private", "private"))
        self.assertFalse(same_instance(WORLD_A, "wrld_00000000-0000-0000-0000-000000000001:other"))
        self.assertTrue(same_instance(WORLD_A, WORLD_A))

    def test_insight_labels_first_record_without_friendship_claim(self):
        payload = self.insights.friend(
            self.tenant_id,
            "usr_friend",
            self.day.isoformat(),
            self.day.isoformat(),
        )
        self.assertIn("first_recorded_at", payload)
        self.assertNotIn("friends_since", payload)
        self.assertEqual(payload["most_visited_worlds"][0]["world_id"], WORLD_B.split(":")[0])

    def test_unknown_friend_is_not_disclosed(self):
        with self.assertRaises(KeyError):
            self.insights.friend(
                self.tenant_id,
                "usr_missing",
                self.day.isoformat(),
                self.day.isoformat(),
            )


if __name__ == "__main__":
    unittest.main()
