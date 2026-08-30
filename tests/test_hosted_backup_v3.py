from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from server.backup_v3 import (
    BACKUP_V3_FIELDS,
    import_tenant_backup,
    stream_tenant_backup,
)
from server.storage import Store


class HostedBackupV3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.directory.name) / "source.sqlite3"))
        self.bootstrap = self.store.bootstrap("Source", "Source collector")
        self.tenant_id = self.bootstrap["tenant_id"]
        self._seed_complete_tenant(self.store, self.bootstrap)

    def tearDown(self) -> None:
        self.directory.cleanup()

    @staticmethod
    def _seed_complete_tenant(store: Store, bootstrap: dict[str, str]) -> None:
        tenant_id = bootstrap["tenant_id"]
        store.ingest(
            tenant_id,
            bootstrap["collector_id"],
            [
                {
                    "id": "usr_alice",
                    "username": "alice",
                    "displayName": "Alice",
                    "status": "active",
                    "location": "wrld_cafe:instance",
                    "bioLinks": ["https://example.test/alice"],
                    "updatedAt": "2026-08-29T12:00:00+00:00",
                }
            ],
            [
                {
                    "client_event_id": "event_alice_online",
                    "friend_id": "usr_alice",
                    "occurred_at": "2026-08-29T12:00:00+00:00",
                    "old_status": "offline",
                    "new_status": "active",
                    "location": "wrld_cafe:instance",
                    "source": "server-api",
                }
            ],
        )
        store.record_raw_fetch(
            tenant_id,
            "GET",
            "/auth/user",
            200,
            "application/json",
            b'{"sample":1}',
        )
        store.record_raw_fetch(
            tenant_id,
            "GET",
            "/auth/user",
            200,
            "application/json",
            b'{"sample":1}',
        )
        with store.lock, store.connection() as db:
            db.execute(
                """INSERT INTO friend_annotations(
                    tenant_id,friend_id,note,pinned,revision,updated_at
                ) VALUES(?,?,?,?,?,?)""",
                (
                    tenant_id,
                    "usr_alice",
                    "Met at the café",
                    1,
                    "revision_1",
                    "2026-08-29T12:05:00+00:00",
                ),
            )
            db.execute(
                """INSERT INTO tags(tenant_id,id,name,color,created_at,updated_at)
                VALUES(?,?,?,?,?,?)""",
                (
                    tenant_id,
                    "tag_close",
                    "Close friends",
                    "#8ac926",
                    "2026-08-29T12:05:00+00:00",
                    "2026-08-29T12:05:00+00:00",
                ),
            )
            db.execute(
                """INSERT INTO friend_tags(tenant_id,friend_id,tag_id,created_at)
                VALUES(?,?,?,?)""",
                (tenant_id, "usr_alice", "tag_close", "2026-08-29T12:06:00+00:00"),
            )
            db.execute(
                """INSERT INTO friend_identity_events(
                    tenant_id,event_id,friend_id,field,old_value,new_value,occurred_at,source
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    tenant_id,
                    "identity_alice",
                    "usr_alice",
                    "display_name",
                    "Alice old",
                    "Alice",
                    "2026-08-29T11:59:00+00:00",
                    "server-api",
                ),
            )
            db.execute(
                """INSERT INTO friend_tracking_events(
                    tenant_id,event_id,friend_id,tracked,occurred_at,source
                ) VALUES(?,?,?,?,?,?)""",
                (
                    tenant_id,
                    "tracking_alice",
                    "usr_alice",
                    1,
                    "2026-08-29T11:58:00+00:00",
                    "server-api",
                ),
            )
            for index in range(3):
                db.execute(
                    """INSERT INTO collection_samples(
                        tenant_id,sample_id,observed_at,source,outcome,authoritative,
                        expected_interval_seconds,friend_count,online_count,duration_ms,
                        error_category
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        tenant_id,
                        f"sample_{index}",
                        f"2026-08-29T12:0{index}:00+00:00",
                        "server-api",
                        "success",
                        1,
                        180,
                        1,
                        1,
                        25 + index,
                        "",
                    ),
                )
            db.execute(
                """INSERT INTO event_anomalies(
                    tenant_id,anomaly_id,event_kind,event_id,reason,detected_at
                ) VALUES(?,?,?,?,?,?)""",
                (
                    tenant_id,
                    "anomaly_alice",
                    "status_event",
                    "event_alice_online",
                    "future_timestamp",
                    "2026-08-29T12:10:00+00:00",
                ),
            )
            db.execute(
                """INSERT INTO tenant_preferences(tenant_id,timezone,updated_at)
                VALUES(?,?,?)""",
                (tenant_id, "Asia/Shanghai", "2026-08-29T12:10:00+00:00"),
            )
            db.execute(
                """INSERT INTO vrchat_accounts(
                    tenant_id,vrchat_user_id,collector_id,display_name,encrypted_cookie,
                    state,credential_updated_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    tenant_id,
                    "usr_owner",
                    bootstrap["collector_id"],
                    "Owner",
                    b"encrypted_cookie_secret",
                    "active",
                    "2026-08-29T12:10:00+00:00",
                    "2026-08-29T12:10:00+00:00",
                ),
            )
            db.execute(
                """INSERT INTO viewer_tokens(
                    id,tenant_id,token_hash,created_at
                ) VALUES(?,?,?,?)""",
                (
                    "viewer_secret",
                    tenant_id,
                    "viewer_token_hash_secret",
                    "2026-08-29T12:10:00+00:00",
                ),
            )

    def _empty_target(self) -> tuple[Store, dict[str, str]]:
        target = Store(str(Path(self.directory.name) / "target.sqlite3"))
        return target, target.bootstrap("Target", "Target collector")

    @staticmethod
    def _counts(store: Store, tenant_id: str) -> dict[str, int]:
        with store.lock, store.connection() as db:
            return {
                field: int(
                    db.execute(
                        f"SELECT COUNT(*) FROM {field} WHERE tenant_id=?", (tenant_id,)
                    ).fetchone()[0]
                )
                for field in BACKUP_V3_FIELDS
            }

    def test_v3_full_round_trip_preserves_raw_and_new_ledgers(self) -> None:
        exported = b"".join(
            stream_tenant_backup(self.store, self.tenant_id, include_raw=True)
        )
        decoded = json.loads(gzip.decompress(exported))
        target, target_bootstrap = self._empty_target()

        result = import_tenant_backup(
            target,
            target_bootstrap["tenant_id"],
            gzip.decompress(exported),
        )

        self.assertEqual(decoded["version"], 3)
        self.assertEqual(decoded["scope"], "full")
        self.assertEqual(
            list(decoded),
            ["format", "version", "scope", "exported_at", *BACKUP_V3_FIELDS],
        )
        self.assertEqual(result["raw_fetches"], 2)
        self.assertEqual(result["collection_samples"], 3)
        self.assertEqual(result["friend_annotations"], 1)
        self.assertEqual(
            self._counts(target, target_bootstrap["tenant_id"]),
            self._counts(self.store, self.tenant_id),
        )
        self.assertEqual(
            import_tenant_backup(target, target_bootstrap["tenant_id"], exported),
            {field: 0 for field in BACKUP_V3_FIELDS},
        )
        with target.lock, target.connection() as db:
            raw = db.execute(
                """SELECT client_fetch_id,body FROM raw_fetches
                WHERE tenant_id=? ORDER BY id""",
                (target_bootstrap["tenant_id"],),
            ).fetchall()
        self.assertEqual(len({row["client_fetch_id"] for row in raw}), 2)
        self.assertEqual([bytes(row["body"]) for row in raw], [b'{"sample":1}'] * 2)

    def test_normalized_scope_keeps_every_ledger_except_raw_fetches(self) -> None:
        exported = b"".join(
            stream_tenant_backup(self.store, self.tenant_id, include_raw=False)
        )
        payload = json.loads(gzip.decompress(exported))

        self.assertEqual(payload["scope"], "normalized")
        self.assertEqual(payload["raw_fetches"], [])
        self.assertEqual(len(payload["collection_samples"]), 3)
        self.assertEqual(len(payload["friend_annotations"]), 1)

    def test_v3_never_exports_authentication_material(self) -> None:
        decoded = gzip.decompress(
            b"".join(stream_tenant_backup(self.store, self.tenant_id, True))
        )

        for forbidden in (
            b"encrypted_cookie",
            b"encrypted_cookie_secret",
            b"viewer_tokens",
            b"viewer_token_hash_secret",
            b"password",
            b"bootstrap_token",
            b"login_codes",
            b"collectors",
            b"vrchat_accounts",
        ):
            self.assertNotIn(forbidden, decoded)

    def test_export_and_import_remain_tenant_scoped(self) -> None:
        other_source = self.store.bootstrap("Other source", "Other collector")
        self.store.ingest(
            other_source["tenant_id"],
            other_source["collector_id"],
            [{"id": "usr_other_source", "displayName": "Other source"}],
            [],
        )
        exported = b"".join(stream_tenant_backup(self.store, self.tenant_id, True))
        payload = json.loads(gzip.decompress(exported))
        target, target_bootstrap = self._empty_target()
        other_target = target.bootstrap("Other target", "Other target collector")
        target.ingest(
            other_target["tenant_id"],
            other_target["collector_id"],
            [{"id": "usr_other_target", "displayName": "Other target"}],
            [],
        )

        import_tenant_backup(target, target_bootstrap["tenant_id"], exported)

        self.assertNotIn("usr_other_source", {row["id"] for row in payload["friends"]})
        with target.lock, target.connection() as db:
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM friends WHERE tenant_id=?",
                    (other_target["tenant_id"],),
                ).fetchone()[0],
                1,
            )
            self.assertIsNotNone(
                db.execute(
                    """SELECT 1 FROM friends
                    WHERE tenant_id=? AND id='usr_other_target'""",
                    (other_target["tenant_id"],),
                ).fetchone()
            )

    def test_raw_timestamp_and_stable_id_are_preserved_exactly(self) -> None:
        target, target_bootstrap = self._empty_target()
        payload = json.loads(
            gzip.decompress(
                b"".join(stream_tenant_backup(self.store, self.tenant_id, True))
            )
        )
        payload["raw_fetches"][0]["occurred_at"] = "2026-08-29T12:00:00Z"
        encoded = json.dumps(payload, separators=(",", ":")).encode()

        self.assertEqual(
            import_tenant_backup(target, target_bootstrap["tenant_id"], encoded)[
                "raw_fetches"
            ],
            2,
        )
        self.assertEqual(
            import_tenant_backup(target, target_bootstrap["tenant_id"], encoded)[
                "raw_fetches"
            ],
            0,
        )
        with target.lock, target.connection() as db:
            row = db.execute(
                """SELECT client_fetch_id,occurred_at FROM raw_fetches
                WHERE tenant_id=? AND client_fetch_id=?""",
                (
                    target_bootstrap["tenant_id"],
                    payload["raw_fetches"][0]["client_fetch_id"],
                ),
            ).fetchone()
        self.assertEqual(row["occurred_at"], "2026-08-29T12:00:00Z")

    def test_invalid_late_reference_rolls_back_entire_staged_import(self) -> None:
        target, target_bootstrap = self._empty_target()
        payload = json.loads(
            gzip.decompress(
                b"".join(stream_tenant_backup(self.store, self.tenant_id, True))
            )
        )
        payload["friends"].append(
            {
                **payload["friends"][0],
                "id": "usr_would_be_partial",
                "username": "partial",
                "display_name": "Partial",
            }
        )
        payload["friend_tags"].append(
            {
                "friend_id": "usr_would_be_partial",
                "tag_id": "tag_missing",
                "created_at": "2026-08-29T13:00:00+00:00",
            }
        )
        before = self._counts(target, target_bootstrap["tenant_id"])

        with self.assertRaisesRegex(ValueError, "引用"):
            import_tenant_backup(
                target,
                target_bootstrap["tenant_id"],
                json.dumps(payload, separators=(",", ":")).encode(),
            )

        self.assertEqual(self._counts(target, target_bootstrap["tenant_id"]), before)

    def test_conflicting_stable_raw_id_rolls_back_other_new_rows(self) -> None:
        target, target_bootstrap = self._empty_target()
        encoded = gzip.decompress(
            b"".join(stream_tenant_backup(self.store, self.tenant_id, True))
        )
        import_tenant_backup(target, target_bootstrap["tenant_id"], encoded)
        changed = json.loads(encoded)
        changed["friends"].append(
            {
                **changed["friends"][0],
                "id": "usr_must_rollback",
                "username": "rollback",
                "display_name": "Rollback",
            }
        )
        changed["raw_fetches"][-1]["body_b64"] = "dGFtcGVyZWQ="
        before = self._counts(target, target_bootstrap["tenant_id"])

        with self.assertRaisesRegex(ValueError, "稳定 ID"):
            import_tenant_backup(
                target,
                target_bootstrap["tenant_id"],
                json.dumps(changed, separators=(",", ":")).encode(),
            )

        self.assertEqual(self._counts(target, target_bootstrap["tenant_id"]), before)
        with target.lock, target.connection() as db:
            self.assertIsNone(
                db.execute(
                    "SELECT 1 FROM friends WHERE tenant_id=? AND id='usr_must_rollback'",
                    (target_bootstrap["tenant_id"],),
                ).fetchone()
            )

    def test_rejects_credential_shaped_root_fields_and_expansion_overflow(self) -> None:
        target, target_bootstrap = self._empty_target()
        payload = json.loads(
            gzip.decompress(
                b"".join(stream_tenant_backup(self.store, self.tenant_id, True))
            )
        )
        payload["viewer_tokens"] = [{"token": "secret"}]
        encoded = json.dumps(payload, separators=(",", ":")).encode()

        with self.assertRaisesRegex(ValueError, "字段"):
            import_tenant_backup(target, target_bootstrap["tenant_id"], encoded)
        with self.assertRaisesRegex(ValueError, "解压后过大"):
            import_tenant_backup(
                target,
                target_bootstrap["tenant_id"],
                gzip.compress(encoded),
                maximum_expanded=128,
            )

        self.assertEqual(
            self._counts(target, target_bootstrap["tenant_id"]),
            {field: 0 for field in BACKUP_V3_FIELDS},
        )


if __name__ == "__main__":
    unittest.main()
