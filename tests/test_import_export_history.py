import copy
import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

from vrchat_monitor.db import Database


class ImportExportHistoryTests(unittest.TestCase):
    def test_history_is_searchable_and_backup_is_merge_only(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(f"{directory}/monitor.sqlite3")
            db.upsert_friends(
                [
                    {
                        "id": "usr_1",
                        "username": "alice",
                        "displayName": "Alice",
                        "status": "active",
                        "location": "wrld_test",
                        "bioLinks": ["https://example.test/alice"],
                    }
                ],
                source="test",
            )
            db.upsert_friends(
                [
                    {
                        "id": "usr_1",
                        "username": "alice",
                        "displayName": "Alice",
                        "status": "offline",
                        "location": "offline",
                        "bioLinks": ["https://example.test/alice"],
                    }
                ],
                source="test",
            )
            page = db.history_page(limit=1, query="alice")
            self.assertEqual(page["total"], 2)
            self.assertEqual(len(page["items"]), 1)
            backup = db.json_export()
            self.assertEqual(backup["format"], "vrchat-monitor-backup")
            self.assertEqual(backup["version"], 2)
            self.assertNotIn("auth_cookie", json.dumps(backup))
            with tempfile.TemporaryDirectory() as second_directory:
                restored = Database(f"{second_directory}/monitor.sqlite3")
                result = restored.json_import(backup)
                self.assertEqual(result["status_events"], 2)
                self.assertEqual(restored.history_page()["total"], 2)
                self.assertEqual(
                    restored.friends()[0]["bio_links"], ["https://example.test/alice"]
                )
                self.assertEqual(restored.json_import(backup)["status_events"], 0)

    def test_raw_fetch_restore_preserves_duplicate_samples_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Database(f"{directory}/source.sqlite3")
            source.record_raw_fetch("GET", "/auth/user", 200, "application/json", b'{"sample":1}')
            source.record_raw_fetch("GET", "/auth/user", 200, "application/json", b'{"sample":2}')
            backup = source.json_export()

            self.assertEqual(len(backup["raw_fetches"]), 2)
            self.assertNotEqual(
                backup["raw_fetches"][0]["client_fetch_id"],
                backup["raw_fetches"][1]["client_fetch_id"],
            )
            with tempfile.TemporaryDirectory() as restored_directory:
                restored = Database(f"{restored_directory}/restored.sqlite3")
                self.assertEqual(restored.json_import(backup)["raw_fetches"], 2)
                self.assertEqual(restored.json_import(backup)["raw_fetches"], 0)
                self.assertEqual(len(restored.raw_fetches(10)), 2)

    def test_legacy_raw_fetch_ids_preserve_same_second_samples(self):
        payload = {
            "format": "vrchat-monitor-backup",
            "version": 1,
            "raw_fetches": [
                {
                    "id": row_id,
                    "occurred_at": "2026-08-27T12:00:00+00:00",
                    "method": "GET",
                    "path": "/auth/user",
                    "status_code": 200,
                    "content_type": "application/json",
                    "body_b64": "e30=",
                    "error": "",
                }
                for row_id in (1, 2)
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            restored = Database(f"{directory}/restored.sqlite3")
            self.assertEqual(restored.json_import(payload)["raw_fetches"], 2)
            self.assertEqual(restored.json_import(payload)["raw_fetches"], 0)
            payload["raw_fetches"][0]["body_b64"] = "eyJkaWZmZXJlbnQiOnRydWV9"
            self.assertEqual(restored.json_import(payload)["raw_fetches"], 1)

    def test_legacy_duplicate_events_and_runs_are_preserved(self):
        payload = {
            "format": "vrchat-monitor-backup",
            "version": 1,
            "friends": [
                {
                    "id": "usr_1",
                    "display_name": "Alice",
                    "updated_at": "2026-08-27T12:00:00+00:00",
                }
            ],
            "status_events": [
                {
                    "id": row_id,
                    "friend_id": "usr_1",
                    "occurred_at": "2026-08-27T12:00:00+00:00",
                    "old_status": "offline",
                    "new_status": "active",
                }
                for row_id in (1, 2)
            ],
            "sync_runs": [
                {
                    "id": row_id,
                    "started_at": "2026-08-27T12:00:00+00:00",
                    "source": "api",
                    "status": "ok",
                    "friend_count": 1,
                }
                for row_id in (1, 2)
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            restored = Database(f"{directory}/restored.sqlite3")
            first = restored.json_import(payload)
            second = restored.json_import(payload)
            self.assertEqual(first["status_events"], 2)
            self.assertEqual(first["sync_runs"], 2)
            self.assertEqual(second["status_events"], 0)
            self.assertEqual(second["sync_runs"], 0)

    def test_existing_append_only_rows_receive_stable_deterministic_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/legacy.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                connection.executescript(
                    """
                    CREATE TABLE status_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        friend_id TEXT NOT NULL,
                        occurred_at TEXT NOT NULL,
                        old_status TEXT NOT NULL,
                        new_status TEXT NOT NULL,
                        location TEXT NOT NULL DEFAULT '',
                        platform TEXT NOT NULL DEFAULT '',
                        source TEXT NOT NULL DEFAULT 'api'
                    );
                    CREATE TABLE sync_runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        started_at TEXT NOT NULL,
                        finished_at TEXT,
                        source TEXT NOT NULL,
                        status TEXT NOT NULL,
                        friend_count INTEGER NOT NULL DEFAULT 0,
                        error TEXT NOT NULL DEFAULT ''
                    );
                    CREATE TABLE raw_fetches (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        occurred_at TEXT NOT NULL,
                        method TEXT NOT NULL,
                        path TEXT NOT NULL,
                        status_code INTEGER,
                        content_type TEXT NOT NULL DEFAULT '',
                        body BLOB NOT NULL DEFAULT X'',
                        error TEXT NOT NULL DEFAULT ''
                    );
                    INSERT INTO status_events
                    (friend_id, occurred_at, old_status, new_status, location, platform, source)
                    VALUES
                    ('usr_1', '2026-08-27T12:00:00+00:00', 'offline', 'active', '', '', 'api'),
                    ('usr_1', '2026-08-27T12:00:00+00:00', 'offline', 'active', '', '', 'api');
                    INSERT INTO sync_runs
                    (started_at, finished_at, source, status, friend_count, error)
                    VALUES
                    ('2026-08-27T12:00:00+00:00', NULL, 'api', 'running', 0, ''),
                    ('2026-08-27T12:00:00+00:00', NULL, 'api', 'running', 0, '');
                    INSERT INTO raw_fetches
                    (occurred_at, method, path, status_code, content_type, body, error)
                    VALUES
                    ('2026-08-27T12:00:00+00:00', 'GET', '/auth/user', 200,
                     'application/json', X'7B7D', ''),
                    ('2026-08-27T12:00:00+00:00', 'GET', '/auth/user', 200,
                     'application/json', X'7B7D', '');
                    """
                )

            migrated = Database(path)
            first_export = migrated.json_export()
            id_sets = {
                "event": [item["client_event_id"] for item in first_export["status_events"]],
                "run": [item["client_run_id"] for item in first_export["sync_runs"]],
                "fetch": [item["client_fetch_id"] for item in first_export["raw_fetches"]],
            }
            for prefix, values in id_sets.items():
                self.assertEqual(len(set(values)), 2)
                self.assertTrue(all(value.startswith(f"legacy_{prefix}_") for value in values))

            reopened = Database(path)
            second_export = reopened.json_export()
            self.assertEqual(
                [item["client_fetch_id"] for item in second_export["raw_fetches"]],
                id_sets["fetch"],
            )
            self.assertEqual(
                [item["client_event_id"] for item in second_export["status_events"]],
                id_sets["event"],
            )
            self.assertEqual(
                [item["client_run_id"] for item in second_export["sync_runs"]],
                id_sets["run"],
            )

    def test_previously_upgraded_rows_do_not_duplicate_when_importing_v1_backup(self):
        payload = {
            "format": "vrchat-monitor-backup",
            "version": 1,
            "friends": [
                {
                    "id": "usr_1",
                    "username": "alice",
                    "display_name": "Alice",
                    "updated_at": "2026-08-27T12:00:00+00:00",
                }
            ],
            "status_events": [
                {
                    "id": 1,
                    "friend_id": "usr_1",
                    "occurred_at": "2026-08-27T12:01:00+00:00",
                    "old_status": "offline",
                    "new_status": "active",
                    "location": "wrld_example",
                    "platform": "standalonewindows",
                    "source": "api",
                }
            ],
            "sync_runs": [
                {
                    "id": 1,
                    "started_at": "2026-08-27T12:00:00+00:00",
                    "finished_at": "2026-08-27T12:02:00+00:00",
                    "source": "api",
                    "status": "ok",
                    "friend_count": 1,
                    "error": "",
                }
            ],
            "raw_fetches": [
                {
                    "id": 1,
                    "occurred_at": "2026-08-27T12:00:30+00:00",
                    "method": "GET",
                    "path": "/auth/user",
                    "status_code": 200,
                    "content_type": "application/json",
                    "body_b64": "e30=",
                    "error": "",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/previously-upgraded.sqlite3"
            db = Database(path)
            db.json_import({
                "format": "vrchat-monitor-backup",
                "version": 2,
                "friends": payload["friends"],
                "status_events": [
                    {
                        **payload["status_events"][0],
                        "client_event_id": "legacy_event_from_previous_release",
                    }
                ],
                "sync_runs": [
                    {
                        **payload["sync_runs"][0],
                        "client_run_id": "legacy_run_from_previous_release",
                    }
                ],
                "raw_fetches": [
                    {
                        **payload["raw_fetches"][0],
                        "client_fetch_id": "legacy_fetch_from_previous_release",
                    }
                ],
            })

            before = db.json_export()
            imported = db.json_import(payload)
            after = db.json_export()

            self.assertEqual(
                imported,
                {"friends": 0, "status_events": 0, "sync_runs": 0, "raw_fetches": 0},
            )
            self.assertEqual(len(after["status_events"]), len(before["status_events"]))
            self.assertEqual(len(after["sync_runs"]), len(before["sync_runs"]))
            self.assertEqual(len(after["raw_fetches"]), len(before["raw_fetches"]))

            fresh = Database(f"{directory}/fresh.sqlite3")
            self.assertEqual(fresh.json_import(payload)["status_events"], 1)
            expected_event_id = Database._legacy_record_id(
                "event",
                1,
                (
                    "usr_1",
                    "2026-08-27T12:01:00+00:00",
                    "offline",
                    "active",
                    "wrld_example",
                    "standalonewindows",
                    "api",
                ),
            )
            self.assertEqual(
                fresh.json_export()["status_events"][0]["client_event_id"],
                expected_event_id,
            )

    def test_previous_and_current_v2_legacy_ids_merge_in_both_orders(self):
        friend = {
            "id": "usr_1",
            "username": "alice",
            "display_name": "Alice",
            "updated_at": "2026-08-27T12:00:00+00:00",
        }
        event_values = (
            "usr_1",
            "2026-08-27T12:01:00+00:00",
            "offline",
            "active",
            "wrld_example",
            "standalonewindows",
            "api",
        )
        run_values = (
            "2026-08-27T12:00:00+00:00",
            "2026-08-27T12:02:00+00:00",
            "api",
            "ok",
            1,
            "",
        )
        body = b'{}'
        fetch_values = (
            "2026-08-27T12:00:30+00:00",
            "GET",
            "/auth/user",
            200,
            "application/json",
            body,
            "",
        )
        fetch_identity = (
            *fetch_values[:5],
            hashlib.sha256(body).hexdigest(),
            fetch_values[6],
        )

        def previous_id(
            table: str,
            prefix: str,
            columns: tuple[str, ...],
            values: tuple[object, ...],
        ) -> str:
            identity = [["id", 7]]
            for name, value in zip(columns, values, strict=True):
                if isinstance(value, bytes):
                    value = {
                        "bytes": len(value),
                        "sha256": hashlib.sha256(value).hexdigest(),
                    }
                identity.append([name, value])
            encoded = json.dumps(
                [table, identity], ensure_ascii=False, separators=(",", ":")
            ).encode()
            return f"legacy_{prefix}_7_{hashlib.sha256(encoded).hexdigest()}"

        columns = {
            "event": (
                "friend_id",
                "occurred_at",
                "old_status",
                "new_status",
                "location",
                "platform",
                "source",
            ),
            "run": (
                "started_at",
                "finished_at",
                "source",
                "status",
                "friend_count",
                "error",
            ),
            "fetch": (
                "occurred_at",
                "method",
                "path",
                "status_code",
                "content_type",
                "body",
                "error",
            ),
        }

        def backup(current: bool) -> dict[str, object]:
            return {
                "format": "vrchat-monitor-backup",
                "version": 2,
                "friends": [friend],
                "status_events": [{
                    "id": 7,
                    "client_event_id": (
                        Database._legacy_record_id("event", 7, event_values)
                        if current
                        else previous_id("status_events", "event", columns["event"], event_values)
                    ),
                    "friend_id": event_values[0],
                    "occurred_at": event_values[1],
                    "old_status": event_values[2],
                    "new_status": event_values[3],
                    "location": event_values[4],
                    "platform": event_values[5],
                    "source": event_values[6],
                }],
                "sync_runs": [{
                    "id": 7,
                    "client_run_id": (
                        Database._legacy_record_id("run", 7, run_values)
                        if current
                        else previous_id("sync_runs", "run", columns["run"], run_values)
                    ),
                    "started_at": run_values[0],
                    "finished_at": run_values[1],
                    "source": run_values[2],
                    "status": run_values[3],
                    "friend_count": run_values[4],
                    "error": run_values[5],
                }],
                "raw_fetches": [{
                    "id": 7,
                    "client_fetch_id": (
                        Database._legacy_record_id("fetch", 7, fetch_identity)
                        if current
                        else previous_id("raw_fetches", "fetch", columns["fetch"], fetch_values)
                    ),
                    "occurred_at": fetch_values[0],
                    "method": fetch_values[1],
                    "path": fetch_values[2],
                    "status_code": fetch_values[3],
                    "content_type": fetch_values[4],
                    "body_b64": "e30=",
                    "error": fetch_values[6],
                }],
            }

        with tempfile.TemporaryDirectory() as directory:
            for label, first, second in (
                ("old-then-current", backup(False), backup(True)),
                ("current-then-old", backup(True), backup(False)),
            ):
                restored = Database(f"{directory}/{label}.sqlite3")
                self.assertEqual(
                    restored.json_import(first),
                    {"friends": 1, "status_events": 1, "sync_runs": 1, "raw_fetches": 1},
                )
                self.assertEqual(
                    restored.json_import(second),
                    {"friends": 0, "status_events": 0, "sync_runs": 0, "raw_fetches": 0},
                )

    def test_database_forks_merge_without_dropping_new_records(self):
        with tempfile.TemporaryDirectory() as directory:
            source_path = f"{directory}/source.sqlite3"
            source = Database(source_path)
            source.record_raw_fetch("GET", "/seed", 200, body=b"seed")

            branch_paths = [f"{directory}/left.sqlite3", f"{directory}/right.sqlite3"]
            for branch_path in branch_paths:
                with closing(sqlite3.connect(source_path)) as original, closing(
                    sqlite3.connect(branch_path)
                ) as branch:
                    original.backup(branch)

            left = Database(branch_paths[0])
            right = Database(branch_paths[1])
            left.record_raw_fetch("GET", "/branch", 200, body=b"left")
            right.record_raw_fetch("GET", "/branch", 200, body=b"right")

            restored = Database(f"{directory}/restored.sqlite3")
            self.assertEqual(restored.json_import(left.json_export())["raw_fetches"], 2)
            self.assertEqual(restored.json_import(right.json_export())["raw_fetches"], 1)
            with closing(sqlite3.connect(restored.path)) as connection:
                bodies = {
                    bytes(row[0])
                    for row in connection.execute("SELECT body FROM raw_fetches").fetchall()
                }
            self.assertEqual(bodies, {b"seed", b"left", b"right"})

    def test_bridge_checkpoint_detects_a_rewound_or_replaced_history(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(f"{directory}/monitor.sqlite3")
            db.upsert_friends(
                [{"id": "usr_1", "displayName": "Alice", "status": "active"}],
                source="test",
            )
            event = db.events_after()[0]

            current = db.bridge_events(event["id"], event["client_event_id"])
            rewound = db.bridge_events(event["id"], "event_from_another_database")

            self.assertFalse(current["reset_required"])
            self.assertEqual(current["items"], [])
            self.assertTrue(rewound["reset_required"])
            self.assertEqual(
                [item["client_event_id"] for item in rewound["items"]],
                [event["client_event_id"]],
            )

    def test_legacy_database_forked_before_upgrade_keeps_shared_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            seed_path = f"{directory}/legacy.sqlite3"
            with closing(sqlite3.connect(seed_path)) as connection:
                connection.executescript(
                    """
                    CREATE TABLE raw_fetches (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        occurred_at TEXT NOT NULL,
                        method TEXT NOT NULL,
                        path TEXT NOT NULL,
                        status_code INTEGER,
                        content_type TEXT NOT NULL DEFAULT '',
                        body BLOB NOT NULL DEFAULT X'',
                        error TEXT NOT NULL DEFAULT ''
                    );
                    INSERT INTO raw_fetches
                    (occurred_at, method, path, status_code, content_type, body, error)
                    VALUES ('2026-08-27T12:00:00+00:00', 'GET', '/shared', 200,
                            'application/json', X'736861726564', '');
                    """
                )
            branches = [f"{directory}/left.sqlite3", f"{directory}/right.sqlite3"]
            for branch_path in branches:
                with closing(sqlite3.connect(seed_path)) as source, closing(
                    sqlite3.connect(branch_path)
                ) as target:
                    source.backup(target)

            for branch_path, path, body in (
                (branches[0], "/left", b"left"),
                (branches[1], "/right", b"right"),
            ):
                with closing(sqlite3.connect(branch_path)) as connection:
                    connection.execute(
                        """INSERT INTO raw_fetches
                        (occurred_at, method, path, status_code, content_type, body, error)
                        VALUES ('2026-08-27T12:01:00+00:00', 'GET', ?, 200,
                                'application/json', ?, '')""",
                        (path, body),
                    )

            left = Database(branches[0])
            right = Database(branches[1])
            left_backup = left.json_export()
            right_backup = right.json_export()

            self.assertEqual(
                left_backup["raw_fetches"][0]["client_fetch_id"],
                right_backup["raw_fetches"][0]["client_fetch_id"],
            )
            restored = Database(f"{directory}/restored.sqlite3")
            self.assertEqual(restored.json_import(left_backup)["raw_fetches"], 2)
            self.assertEqual(restored.json_import(right_backup)["raw_fetches"], 1)
            with closing(sqlite3.connect(restored.path)) as connection:
                bodies = [
                    bytes(row[0])
                    for row in connection.execute(
                        "SELECT body FROM raw_fetches ORDER BY id"
                    ).fetchall()
                ]
            self.assertEqual(bodies, [b"shared", b"left", b"right"])

    def test_conflicting_stable_id_rolls_back_the_whole_import(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Database(f"{directory}/source.sqlite3")
            source.upsert_friends(
                [{"id": "usr_1", "username": "alice", "displayName": "Alice"}],
                source="test",
            )
            source.record_raw_fetch("GET", "/auth/user", 200, body=b"original")
            backup = source.json_export()

            restored = Database(f"{directory}/restored.sqlite3")
            restored.json_import(backup)
            changed = copy.deepcopy(backup)
            changed["friends"][0]["display_name"] = "Tampered"
            changed["friends"][0]["updated_at"] = "9999-12-31T23:59:59+00:00"
            changed["raw_fetches"][0]["body_b64"] = "dGFtcGVyZWQ="

            with self.assertRaisesRegex(ValueError, "稳定 ID"):
                restored.json_import(changed)
            self.assertEqual(restored.friends()[0]["display_name"], "Alice")
            with closing(sqlite3.connect(restored.path)) as connection:
                body = bytes(connection.execute("SELECT body FROM raw_fetches").fetchone()[0])
                count = connection.execute("SELECT COUNT(*) FROM raw_fetches").fetchone()[0]
            self.assertEqual(body, b"original")
            self.assertEqual(count, 1)

    def test_older_backup_does_not_replace_a_newer_friend_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            restored = Database(f"{directory}/restored.sqlite3")
            newer = {
                "format": "vrchat-monitor-backup",
                "version": 1,
                "friends": [
                    {
                        "id": "usr_1",
                        "display_name": "Fresh",
                        "status": "active",
                        "updated_at": "2026-08-27T13:00:00+00:00",
                    }
                ],
            }
            older = copy.deepcopy(newer)
            older["friends"][0].update(
                {
                    "display_name": "Stale",
                    "status": "offline",
                    "updated_at": "2026-08-27T12:00:00+00:00",
                }
            )
            tied = copy.deepcopy(newer)
            tied["friends"][0].update({"display_name": "Tie", "status": "offline"})

            self.assertEqual(restored.json_import(newer)["friends"], 1)
            self.assertEqual(restored.json_import(older)["friends"], 0)
            with self.assertRaisesRegex(ValueError, "时间相同但内容不同"):
                restored.json_import(tied)
            friend = restored.friends()[0]
            self.assertEqual(friend["display_name"], "Fresh")
            self.assertEqual(friend["status"], "active")

    def test_large_raw_response_round_trips_without_a_hidden_size_cutoff(self):
        body = b"x" * (8 * 1024 * 1024 + 1)
        expected_digest = hashlib.sha256(body).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            source = Database(f"{directory}/source.sqlite3")
            source.record_raw_fetch("GET", "/large", 200, body=body)
            backup = source.json_export()
            restored = Database(f"{directory}/restored.sqlite3")
            self.assertEqual(restored.json_import(backup)["raw_fetches"], 1)
            with closing(sqlite3.connect(restored.path)) as connection:
                restored_body = bytes(
                    connection.execute("SELECT body FROM raw_fetches").fetchone()[0]
                )
            self.assertEqual(hashlib.sha256(restored_body).hexdigest(), expected_digest)

    def test_invalid_raw_body_rejects_and_rolls_back_legacy_backup(self):
        payload = {
            "format": "vrchat-monitor-backup",
            "version": 1,
            "friends": [{"id": "usr_1", "username": "alice"}],
            "raw_fetches": [
                {
                    "id": 1,
                    "occurred_at": "2026-08-27T12:00:00+00:00",
                    "body_b64": "not-base64!",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            restored = Database(f"{directory}/restored.sqlite3")
            with self.assertRaisesRegex(ValueError, "Base64"):
                restored.json_import(payload)
            self.assertEqual(restored.friends(), [])

    def test_invalid_append_timestamps_and_self_flag_fail_closed(self):
        friend = {
            "id": "usr_1",
            "username": "alice",
            "updated_at": "2026-08-27T12:00:00+00:00",
        }
        cases = {
            "本人标记": {
                "friends": [{**friend, "is_self": "false"}],
            },
            "状态记录时间": {
                "friends": [friend],
                "status_events": [
                    {
                        "id": 1,
                        "friend_id": "usr_1",
                        "occurred_at": "not-a-time",
                    }
                ],
            },
            "同步开始时间": {
                "sync_runs": [{"id": 1, "started_at": "not-a-time"}],
            },
            "同步结束时间": {
                "sync_runs": [
                    {
                        "id": 1,
                        "started_at": "2026-08-27T12:00:00+00:00",
                        "finished_at": "not-a-time",
                    }
                ],
            },
            "原始响应时间": {
                "raw_fetches": [
                    {"id": 1, "occurred_at": "not-a-time", "body_b64": "e30="}
                ],
            },
        }
        for expected, collections in cases.items():
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                restored = Database(f"{directory}/restored.sqlite3")
                payload = {
                    "format": "vrchat-monitor-backup",
                    "version": 1,
                    **collections,
                }
                with self.assertRaisesRegex(ValueError, expected):
                    restored.json_import(payload)
                exported = restored.json_export()
                self.assertEqual(exported["friends"], [])
                self.assertEqual(exported["status_events"], [])
                self.assertEqual(exported["sync_runs"], [])
                self.assertEqual(exported["raw_fetches"], [])

    def test_v2_append_records_require_stable_ids(self):
        payload = {
            "format": "vrchat-monitor-backup",
            "version": 2,
            "raw_fetches": [
                {
                    "occurred_at": "2026-08-27T12:00:00+00:00",
                    "body_b64": "e30=",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            restored = Database(f"{directory}/restored.sqlite3")
            with self.assertRaisesRegex(ValueError, "稳定 ID"):
                restored.json_import(payload)

    def test_concurrent_initialization_serializes_legacy_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/legacy.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                connection.executescript(
                    """
                    CREATE TABLE raw_fetches (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        occurred_at TEXT NOT NULL,
                        method TEXT NOT NULL,
                        path TEXT NOT NULL,
                        status_code INTEGER,
                        content_type TEXT NOT NULL DEFAULT '',
                        body BLOB NOT NULL DEFAULT X'',
                        error TEXT NOT NULL DEFAULT ''
                    );
                    INSERT INTO raw_fetches
                    (occurred_at, method, path, status_code, content_type, body, error)
                    VALUES ('2026-08-27T12:00:00+00:00', 'GET', '/auth/user', 200, '', X'7B7D', '');
                    """
                )
            workers = 6
            barrier = threading.Barrier(workers)

            def initialize() -> str:
                barrier.wait()
                return Database(path).json_export()["raw_fetches"][0]["client_fetch_id"]

            with ThreadPoolExecutor(max_workers=workers) as executor:
                ids = list(executor.map(lambda _: initialize(), range(workers)))
            self.assertEqual(len(set(ids)), 1)

    def test_export_starts_an_explicit_snapshot_transaction(self):
        class TracedDatabase(Database):
            def __init__(self, path: str):
                self.statements: list[str] = []
                super().__init__(path)

            def _connect(self) -> sqlite3.Connection:
                connection = super()._connect()
                connection.set_trace_callback(self.statements.append)
                return connection

        with tempfile.TemporaryDirectory() as directory:
            database = TracedDatabase(f"{directory}/monitor.sqlite3")
            database.statements.clear()
            database.json_export()
            statements = [statement.strip().upper() for statement in database.statements]
            begin_index = statements.index("BEGIN")
            first_select = next(
                index
                for index, statement in enumerate(statements)
                if statement.startswith("SELECT * FROM FRIENDS")
            )
            self.assertLess(begin_index, first_select)


if __name__ == "__main__":
    unittest.main()
