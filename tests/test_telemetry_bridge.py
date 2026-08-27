from __future__ import annotations

import io
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from scripts.publish_telemetry import (
    BridgeHTTPError,
    _request_bytes,
    collect_events,
    collect_checkpointed_events,
    collect_legacy_csv,
    legacy_prefix_digest,
    normalize_event,
    publish,
    read_bridge_state,
    read_state,
    validate_urls,
    write_state,
)


class TelemetryBridgeTests(unittest.TestCase):
    def test_http_error_responses_are_closed_on_retry_and_failure(self):
        retry_body = io.BytesIO(b"wait")
        retry_error = urllib.error.HTTPError(
            "https://presence.example/v1/telemetry",
            429,
            "Too Many Requests",
            {"Retry-After": "0"},
            retry_body,
        )
        response = io.BytesIO(b"accepted")
        with (
            patch(
                "scripts.publish_telemetry.urllib.request.urlopen",
                side_effect=[retry_error, response],
            ),
            patch("scripts.publish_telemetry.time.sleep"),
        ):
            self.assertEqual(
                _request_bytes("https://presence.example/v1/telemetry", attempts=2),
                b"accepted",
            )
        self.assertTrue(retry_body.closed)

        terminal_body = io.BytesIO(b"denied")
        terminal_error = urllib.error.HTTPError(
            "https://presence.example/v1/telemetry",
            403,
            "Forbidden",
            {},
            terminal_body,
        )
        with (
            patch(
                "scripts.publish_telemetry.urllib.request.urlopen",
                side_effect=terminal_error,
            ),
            self.assertRaises(BridgeHTTPError),
        ):
            _request_bytes("https://presence.example/v1/telemetry", attempts=1)
        self.assertTrue(terminal_body.closed)

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
        normalized = normalize_event(event)
        self.assertEqual(normalized["client_event_id"], "local-42")
        self.assertEqual(normalized["previous_event_ids"], [])

        migrated_id = f"legacy_event_42_{'a' * 64}"
        migrated = normalize_event(
            {**event, "client_event_id": migrated_id}
        )
        self.assertEqual(migrated["client_event_id"], migrated_id)
        self.assertEqual(migrated["previous_event_ids"], ["local-42"])

        unrelated = normalize_event(
            {**event, "client_event_id": f"legacy_event_41_{'b' * 64}"}
        )
        generated = normalize_event(
            {**event, "client_event_id": "event_0123456789abcdef0123456789abcdef"}
        )
        self.assertEqual(unrelated["previous_event_ids"], [])
        self.assertEqual(generated["previous_event_ids"], [])

    def test_bridge_state_is_private_and_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "state.json"
            write_state(path, 81, last_event_client_id="event_checkpoint")
            self.assertEqual(read_state(path), 81)
            self.assertEqual(
                read_bridge_state(path)["last_event_client_id"],
                "event_checkpoint",
            )
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_checkpointed_history_resets_after_a_database_restore(self):
        calls = []

        def fetch(after_id, checkpoint, limit):
            calls.append((after_id, checkpoint, limit))
            return {
                "reset_required": True,
                "items": [
                    {"id": 1, "client_event_id": "event_1"},
                    {"id": 2, "client_event_id": "event_2"},
                ],
                "has_more": False,
            }

        events, reset = collect_checkpointed_events(fetch, 100, "event_100")

        self.assertTrue(reset)
        self.assertEqual([event["id"] for event in events], [1, 2])
        self.assertEqual(calls, [(100, "event_100", 1000)])

    def test_publish_replays_stable_events_and_replaces_a_rewound_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            write_state(path, 100, last_event_client_id="event_old_100")
            remote_payloads = []

            def request(url, *, payload=None, token="", attempts=4):
                del token, attempts
                if url.endswith("/api/state"):
                    return {"friends": []}
                if "/api/bridge-events?" in url:
                    return {
                        "reset_required": True,
                        "items": [
                            {
                                "id": 1,
                                "client_event_id": "event_stable_1",
                                "friend_id": "usr_1",
                                "occurred_at": "2026-08-27T12:00:00+00:00",
                                "new_status": "active",
                            }
                        ],
                        "has_more": False,
                    }
                if url.endswith("/v1/telemetry"):
                    remote_payloads.append(payload)
                    return {"friends": 0, "events": 0, "changed": 0}
                raise AssertionError(url)

            with patch("scripts.publish_telemetry._json_request", side_effect=request):
                publish(
                    "http://127.0.0.1:8842",
                    "https://presence.example",
                    "collector-token",
                    path,
                )

            self.assertEqual(
                remote_payloads[0]["events"][0]["client_event_id"],
                "event_stable_1",
            )
            state = read_bridge_state(path)
            self.assertEqual(state["last_event_id"], 1)
            self.assertEqual(state["last_event_client_id"], "event_stable_1")

    def test_legacy_csv_has_stable_ids_and_append_only_prefix(self):
        raw = (
            "friend_id,display_name,occurred_at,old_status,new_status,location,platform,source\n"
            "usr_1,One,2026-08-27T12:00:00+00:00,offline,active,wrld_1,standalone,api\n"
            "usr_1,One,2026-08-27T12:00:00+00:00,offline,active,wrld_1,standalone,api\n"
        ).encode()
        events = collect_legacy_csv(raw)

        self.assertEqual(len(events), 2)
        self.assertNotEqual(events[0]["client_event_id"], events[1]["client_event_id"])
        self.assertEqual(collect_legacy_csv(raw), events)
        prefix = legacy_prefix_digest(events)
        extended = collect_legacy_csv(
            raw
            + b"usr_2,Two,2026-08-27T12:01:00+00:00,offline,active,private,web,api\n"
        )
        self.assertEqual(legacy_prefix_digest(extended, len(events)), prefix)

    def test_legacy_bridge_state_preserves_prefix_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            write_state(
                path,
                0,
                mode="legacy-csv",
                legacy_count=12,
                legacy_prefix_sha256="a" * 64,
            )

            state = read_bridge_state(path)
            self.assertEqual(state["mode"], "legacy-csv")
            self.assertEqual(state["legacy_count"], 12)
            self.assertEqual(state["legacy_prefix_sha256"], "a" * 64)

    def test_remote_requires_https_and_local_defaults_to_loopback(self):
        validate_urls("http://127.0.0.1:8842", "https://presence.example")
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            validate_urls("http://127.0.0.1:8842", "http://presence.example")
        with self.assertRaisesRegex(ValueError, "本机采集器"):
            validate_urls("http://collector.example", "https://presence.example")

if __name__ == "__main__":
    unittest.main()
