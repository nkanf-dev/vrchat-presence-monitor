from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.publish_telemetry import collect_events, normalize_event, read_state, validate_urls, write_state


class TelemetryBridgeTests(unittest.TestCase):
    def test_history_collection_stops_at_cursor_and_returns_oldest_first(self):
        pages = {
            0: {"items": [{"id": 5}, {"id": 4}], "has_more": True},
            2: {"items": [{"id": 3}, {"id": 2}], "has_more": True},
            4: {"items": [{"id": 1}], "has_more": False},
        }

        result = collect_events(lambda offset, _: pages[offset], cursor=2)

        self.assertEqual([item["id"] for item in result], [3, 4, 5])

    def test_event_ids_are_stable_across_retries(self):
        event = {
            "id": 42,
            "friend_id": "usr_1",
            "occurred_at": "2026-08-27T12:00:00+00:00",
            "new_status": "active",
        }
        self.assertEqual(normalize_event(event)["client_event_id"], "local-42")

    def test_bridge_state_is_private_and_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "state.json"
            write_state(path, 81)
            self.assertEqual(read_state(path), 81)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_remote_requires_https_and_local_defaults_to_loopback(self):
        validate_urls("http://127.0.0.1:8842", "https://presence.example")
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            validate_urls("http://127.0.0.1:8842", "http://presence.example")
        with self.assertRaisesRegex(ValueError, "本机采集器"):
            validate_urls("http://collector.example", "https://presence.example")


if __name__ == "__main__":
    unittest.main()
