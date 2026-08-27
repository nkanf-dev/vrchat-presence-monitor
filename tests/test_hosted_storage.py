from __future__ import annotations

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
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

    def test_import_does_not_truncate_large_history_and_reexport_is_idempotent(self):
        assert self.collector is not None
        payload = {
            "format": "vrchat-monitor-backup",
            "version": 2,
            "friends": [
                {
                    "id": "usr_1",
                    "display_name": "Alice",
                    "bio_links": '["https://example.test/alice"]',
                    "updated_at": "2026-08-27T12:00:00+00:00",
                }
            ],
            "status_events": [
                {
                    "client_event_id": f"event_{index:032x}",
                    "friend_id": "usr_1",
                    "occurred_at": f"2026-08-27T12:{index % 60:02d}:00+00:00",
                    "old_status": "offline",
                    "new_status": "active",
                }
                for index in range(10001)
            ],
        }

        first = self.store.import_json(self.collector["tenant_id"], payload)
        exported = self.store.export_json(self.collector["tenant_id"])
        second = self.store.import_json(self.collector["tenant_id"], exported)

        self.assertEqual(first["events"], 10001)
        self.assertEqual(second["events"], 0)
        self.assertEqual(exported["version"], 2)
        self.assertNotIn("tenant_id", exported["friends"][0])
        self.assertNotIn("tenant_id", exported["status_events"][0])
        self.assertEqual(
            json.loads(exported["friends"][0]["bio_links"]),
            ["https://example.test/alice"],
        )

    def test_import_event_id_conflict_rolls_back_friend_update(self):
        assert self.collector is not None
        payload = {
            "format": "vrchat-monitor-backup",
            "version": 2,
            "friends": [
                {
                    "id": "usr_1",
                    "display_name": "Alice",
                    "updated_at": "2026-08-27T12:00:00+00:00",
                }
            ],
            "status_events": [
                {
                    "client_event_id": "event_0123456789abcdef0123456789abcdef",
                    "friend_id": "usr_1",
                    "occurred_at": "2026-08-27T12:00:00+00:00",
                    "old_status": "offline",
                    "new_status": "active",
                }
            ],
        }
        self.store.import_json(self.collector["tenant_id"], payload)
        conflicting = {
            **payload,
            "friends": [
                {
                    **payload["friends"][0],
                    "display_name": "Tampered",
                    "updated_at": "2026-08-27T13:00:00+00:00",
                }
            ],
            "status_events": [
                {**payload["status_events"][0], "new_status": "offline"}
            ],
        }

        with self.assertRaisesRegex(ValueError, "稳定 ID"):
            self.store.import_json(self.collector["tenant_id"], conflicting)
        friend = self.store.friends_page(self.collector["tenant_id"])["items"][0]
        self.assertEqual(friend["display_name"], "Alice")

    def test_telemetry_batch_limit_fails_instead_of_truncating(self):
        assert self.collector is not None
        events = [
            {
                "client_event_id": f"event-{index}",
                "friend_id": "usr_1",
                "occurred_at": "2026-08-27T12:00:00+00:00",
            }
            for index in range(10001)
        ]
        with self.assertRaisesRegex(ValueError, "单批历史记录"):
            self.store.ingest(
                self.collector["tenant_id"],
                self.collector["id"],
                [],
                events,
            )

    def test_backup_import_and_migrated_collector_replay_share_one_event_identity(self):
        assert self.collector is not None
        self.ingest("usr_1", "Alice")
        event = {
            "client_event_id": "local-42",
            "friend_id": "usr_1",
            "occurred_at": "2026-08-27T12:00:00+00:00",
            "old_status": "offline",
            "new_status": "active",
            "source": "local-bridge",
        }
        imported = self.store.ingest(
            self.collector["tenant_id"], self.collector["id"], [], [event], "local-bridge"
        )
        migrated_id = f"legacy_event_42_{'a' * 64}"
        replayed = self.store.ingest(
            self.collector["tenant_id"],
            self.collector["id"],
            [],
            [
                {
                    **event,
                    "client_event_id": migrated_id,
                    "previous_event_ids": ["local-42"],
                }
            ],
            "local-bridge",
        )
        exported = self.store.export_json(self.collector["tenant_id"])

        self.assertEqual(imported["events"], 1)
        self.assertEqual(replayed["events"], 0)
        self.assertEqual(len(exported["status_events"]), 1)
        self.assertEqual(exported["status_events"][0]["client_event_id"], "local-42")

    def test_arbitrary_event_alias_is_rejected_without_deduplicating_new_data(self):
        assert self.collector is not None
        self.ingest("usr_1", "Alice")
        with self.assertRaisesRegex(ValueError, "旧稳定 ID"):
            self.store.ingest(
                self.collector["tenant_id"],
                self.collector["id"],
                [],
                [
                    {
                        "client_event_id": "event_new",
                        "previous_event_ids": ["local-42"],
                        "friend_id": "usr_1",
                        "occurred_at": "2026-08-27T12:00:00+00:00",
                    }
                ],
            )
        self.assertEqual(self.store.events_page(self.collector["tenant_id"])["total"], 0)

    def test_migration_alias_survives_hosted_export_restore_and_old_bridge_replay(self):
        assert self.collector is not None
        self.ingest("usr_1", "Alice")
        migrated_id = f"legacy_event_42_{'c' * 64}"
        event = {
            "client_event_id": migrated_id,
            "previous_event_ids": ["local-42"],
            "friend_id": "usr_1",
            "occurred_at": "2026-08-27T12:00:00+00:00",
            "old_status": "offline",
            "new_status": "active",
            "source": "local-bridge",
        }
        self.assertEqual(
            self.store.ingest(
                self.collector["tenant_id"], self.collector["id"], [], [event], "local-bridge"
            )["events"],
            1,
        )
        exported = self.store.export_json(self.collector["tenant_id"])
        self.assertEqual(exported["status_events"][0]["previous_event_ids"], ["local-42"])

        with tempfile.TemporaryDirectory() as directory:
            restored = Store(f"{directory}/restored.sqlite3")
            target = restored.bootstrap("Restored", "bridge")
            restored.import_json(target["tenant_id"], exported)
            replayed = restored.ingest(
                target["tenant_id"],
                target["collector_id"],
                [],
                [{**event, "client_event_id": "local-42", "previous_event_ids": []}],
                "local-bridge",
            )
            round_trip = restored.export_json(target["tenant_id"])
        self.assertEqual(replayed["events"], 0)
        self.assertEqual(len(round_trip["status_events"]), 1)
        self.assertEqual(round_trip["status_events"][0]["client_event_id"], migrated_id)

    def test_local_v1_row_id_is_upgraded_to_a_bound_alias(self):
        assert self.collector is not None
        payload = {
            "format": "vrchat-monitor-backup",
            "version": 1,
            "friends": [{"id": "usr_1", "display_name": "Alice"}],
            "status_events": [{
                "id": 42,
                "friend_id": "usr_1",
                "occurred_at": "2026-08-27T12:00:00+00:00",
                "old_status": "offline",
                "new_status": "active",
            }],
        }
        imported = self.store.import_json(self.collector["tenant_id"], payload)
        exported_event = self.store.export_json(
            self.collector["tenant_id"]
        )["status_events"][0]
        replayed = self.store.ingest(
            self.collector["tenant_id"],
            self.collector["id"],
            [],
            [{**payload["status_events"][0], "client_event_id": "local-42"}],
            "import",
        )
        self.assertEqual(imported["events"], 1)
        self.assertRegex(exported_event["client_event_id"], rf"^legacy_event_42_[0-9a-f]{{64}}$")
        self.assertEqual(exported_event["previous_event_ids"], ["local-42"])
        self.assertEqual(replayed["events"], 0)

    def test_hosted_v1_strips_only_its_outer_namespace_and_v2_preserves_ids(self):
        assert self.collector is not None
        base = {
            "friends": [
                {
                    "id": "usr_1",
                    "display_name": "Alice",
                    "updated_at": "2026-08-27T12:00:00+00:00",
                }
            ],
            "status_events": [
                {
                    "client_event_id": "col_old:col_legit:event",
                    "friend_id": "usr_1",
                    "occurred_at": "2026-08-27T12:00:00+00:00",
                    "old_status": "offline",
                    "new_status": "active",
                }
            ],
        }
        imported_v1 = self.store.import_json(
            self.collector["tenant_id"],
            {**base, "format": "vrchat-monitor-hosted-backup", "version": 1},
        )
        imported_v2 = self.store.import_json(
            self.collector["tenant_id"],
            {
                **base,
                "format": "vrchat-monitor-hosted-backup",
                "version": 2,
                "status_events": [
                    {**base["status_events"][0], "client_event_id": "col_legit:event"}
                ],
            },
        )
        event_ids = [
            item["client_event_id"]
            for item in self.store.export_json(self.collector["tenant_id"])["status_events"]
        ]
        self.assertEqual(imported_v1["events"], 1)
        self.assertEqual(imported_v2["events"], 0)
        self.assertEqual(event_ids, ["col_legit:event"])

    def test_old_collector_prefix_migration_is_exact_and_runs_only_once(self):
        assert self.collector is not None
        self.ingest("usr_1", "Alice")
        old_id = f"{self.collector['id']}:col_legit:event"
        with self.store.connection() as db:
            db.execute("DELETE FROM schema_meta WHERE key='canonical_event_ids_v2'")
            db.execute(
                """INSERT INTO status_events(
                    tenant_id,client_event_id,friend_id,occurred_at,old_status,new_status,
                    location,platform,source
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    self.collector["tenant_id"], old_id, "usr_1",
                    "2026-08-27T12:00:00+00:00", "offline", "active", "", "", "legacy",
                ),
            )
        barrier = threading.Barrier(2)
        def reopen() -> Store:
            barrier.wait()
            return Store(self.store.path)

        with ThreadPoolExecutor(max_workers=2) as executor:
            migrated, concurrent = [
                future.result()
                for future in [executor.submit(reopen), executor.submit(reopen)]
            ]
        first_ids = [
            item["client_event_id"]
            for item in migrated.export_json(self.collector["tenant_id"])["status_events"]
        ]
        self.assertEqual(
            concurrent.export_json(self.collector["tenant_id"])["status_events"][0]["client_event_id"],
            "col_legit:event",
        )
        future_id = f"{self.collector['id']}:future-canonical"
        migrated.ingest(
            self.collector["tenant_id"], self.collector["id"], [],
            [{
                "client_event_id": future_id,
                "friend_id": "usr_1",
                "occurred_at": "2026-08-27T13:00:00+00:00",
            }],
        )
        reopened = Store(self.store.path)
        second_ids = [
            item["client_event_id"]
            for item in reopened.export_json(self.collector["tenant_id"])["status_events"]
        ]
        self.assertEqual(first_ids, ["col_legit:event"])
        self.assertIn(future_id, second_ids)

    def test_maximum_telemetry_event_id_can_be_exported_and_restored(self):
        assert self.collector is not None
        event_id = "e" * 256
        self.ingest("usr_1", "Alice")
        self.store.ingest(
            self.collector["tenant_id"],
            self.collector["id"],
            [],
            [
                {
                    "client_event_id": event_id,
                    "friend_id": "usr_1",
                    "occurred_at": "2026-08-27T12:00:00+00:00",
                }
            ],
        )
        exported = self.store.export_json(self.collector["tenant_id"])

        with tempfile.TemporaryDirectory() as directory:
            restored = Store(f"{directory}/restored.sqlite3")
            target = restored.bootstrap("Restored", "bridge")
            result = restored.import_json(target["tenant_id"], exported)
            round_trip = restored.export_json(target["tenant_id"])
        self.assertEqual(result["events"], 1)
        self.assertEqual(round_trip["status_events"][0]["client_event_id"], event_id)

    def test_backup_oversized_fields_fail_without_truncation(self):
        assert self.collector is not None
        cases = {
            "玩家 ID": {"id": "u" * 129},
            "显示名称": {"id": "usr_1", "display_name": "n" * 257},
            "简介": {"id": "usr_1", "bio": "b" * 8193},
            "位置": {"id": "usr_1", "location": "l" * 1025},
        }
        for message, friend in cases.items():
            with self.subTest(field=message):
                payload = {
                    "format": "vrchat-monitor-backup",
                    "version": 2,
                    "friends": [friend],
                    "status_events": [],
                }
                with self.assertRaisesRegex(ValueError, message):
                    self.store.import_json(self.collector["tenant_id"], payload)
                self.assertEqual(
                    self.store.friends_page(self.collector["tenant_id"])["total"],
                    0,
                )

    def test_older_backup_does_not_downgrade_hosted_friend_snapshot(self):
        assert self.collector is not None
        current = {
            "format": "vrchat-monitor-backup",
            "version": 2,
            "friends": [
                {
                    "id": "usr_1",
                    "display_name": "Fresh",
                    "status": "active",
                    "updated_at": "2026-08-27T13:00:00+00:00",
                }
            ],
            "status_events": [],
        }
        stale = {
            **current,
            "friends": [
                {
                    **current["friends"][0],
                    "display_name": "Stale",
                    "status": "offline",
                    "updated_at": "2026-08-27T12:00:00+00:00",
                }
            ],
        }
        tied = {
            **current,
            "friends": [
                {
                    **current["friends"][0],
                    "display_name": "Tie",
                    "status": "offline",
                }
            ],
        }

        self.store.import_json(self.collector["tenant_id"], current)
        result = self.store.import_json(self.collector["tenant_id"], stale)
        with self.assertRaisesRegex(ValueError, "时间相同但内容不同"):
            self.store.import_json(self.collector["tenant_id"], tied)
        friend = self.store.friends_page(self.collector["tenant_id"])["items"][0]
        self.assertEqual(result["friends"], 0)
        self.assertEqual(friend["display_name"], "Fresh")
        self.assertEqual(friend["status"], "active")

    def test_subsecond_friend_timestamp_keeps_the_newest_snapshot(self):
        assert self.collector is not None
        first = {
            "format": "vrchat-monitor-backup",
            "version": 2,
            "friends": [{
                "id": "usr_1",
                "display_name": "Early",
                "updated_at": "2026-08-27T13:00:00.100000+00:00",
            }],
            "status_events": [],
        }
        second = {
            **first,
            "friends": [{
                **first["friends"][0],
                "display_name": "Late",
                "updated_at": "2026-08-27T13:00:00.900000+00:00",
            }],
        }
        self.store.import_json(self.collector["tenant_id"], first)
        self.store.import_json(self.collector["tenant_id"], second)
        friend = self.store.friends_page(self.collector["tenant_id"])["items"][0]
        self.assertEqual(friend["display_name"], "Late")
        self.assertEqual(friend["updated_at"], "2026-08-27T13:00:00.900000+00:00")

    def test_backup_capacity_rejects_new_data_before_export_becomes_unrestorable(self):
        with tempfile.TemporaryDirectory() as directory:
            maximum = 2_200
            source = Store(f"{directory}/source.sqlite3", max_backup_bytes=maximum)
            account = source.bootstrap("Source", "bridge")
            source.ingest(
                account["tenant_id"],
                account["collector_id"],
                [{"id": "usr_1", "displayName": "Alice"}],
                [],
            )
            accepted = 0
            for index in range(100):
                try:
                    source.ingest(
                        account["tenant_id"],
                        account["collector_id"],
                        [],
                        [
                            {
                                "client_event_id": f"event-{index}",
                                "friend_id": "usr_1",
                                "occurred_at": f"2026-08-27T12:{index % 60:02d}:00+00:00",
                            }
                        ],
                    )
                    accepted += 1
                except ValueError as error:
                    self.assertIn("可恢复备份容量", str(error))
                    break
            else:
                self.fail("backup capacity was never enforced")

            exported = source.export_json(account["tenant_id"])
            encoded = json.dumps(
                exported, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            self.assertGreater(accepted, 0)
            self.assertLessEqual(len(encoded), maximum)

            restored = Store(f"{directory}/restored.sqlite3", max_backup_bytes=maximum)
            target = restored.bootstrap("Target", "bridge")
            result = restored.import_json(target["tenant_id"], exported)
            self.assertEqual(result["events"], accepted)

    def test_two_store_processes_serialize_usage_and_event_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/shared.sqlite3"
            first = Store(path)
            account = first.bootstrap("Shared", "bridge")
            first.ingest(
                account["tenant_id"],
                account["collector_id"],
                [{"id": "usr_1", "displayName": "Alice"}],
                [],
            )
            second = Store(path)
            barrier = threading.Barrier(2)

            def ingest(store: Store, event_id: str) -> dict[str, int]:
                barrier.wait()
                return store.ingest(
                    account["tenant_id"],
                    account["collector_id"],
                    [],
                    [
                        {
                            "client_event_id": event_id,
                            "friend_id": "usr_1",
                            "occurred_at": "2026-08-27T12:00:00+00:00",
                        }
                    ],
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(ingest, first, "event-left"),
                    executor.submit(ingest, second, "event-right"),
                ]
                results = [future.result() for future in futures]

            exported = first.export_json(account["tenant_id"])
            with first.connection() as db:
                usage = db.execute(
                    "SELECT event_count FROM portable_backup_usage WHERE tenant_id=?",
                    (account["tenant_id"],),
                ).fetchone()
            self.assertEqual(sum(result["events"] for result in results), 2)
            self.assertEqual(len(exported["status_events"]), 2)
            self.assertEqual(int(usage["event_count"]), 2)

    def test_import_rejects_ambiguous_self_flags_and_rolls_back(self):
        assert self.collector is not None
        payload = {
            "format": "vrchat-monitor-hosted-backup",
            "version": 2,
            "friends": [
                {
                    "id": "usr_valid",
                    "display_name": "Valid",
                    "is_self": 0,
                    "updated_at": "2026-08-27T12:00:00+00:00",
                },
                {
                    "id": "usr_ambiguous",
                    "display_name": "Ambiguous",
                    "is_self": "false",
                    "updated_at": "2026-08-27T12:00:00+00:00",
                },
            ],
            "status_events": [],
        }

        with self.assertRaisesRegex(ValueError, "本人标记"):
            self.store.import_json(self.collector["tenant_id"], payload)

        page = self.store.friends_page(self.collector["tenant_id"])
        self.assertEqual(page["total"], 0)


if __name__ == "__main__":
    unittest.main()
