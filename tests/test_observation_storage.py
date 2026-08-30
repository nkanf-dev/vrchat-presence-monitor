from __future__ import annotations

import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from server.storage import Store


EXPECTED_TABLES = {
    "collection_samples",
    "event_anomalies",
    "friend_annotations",
    "friend_identity_events",
    "friend_tags",
    "friend_tracking_events",
    "tags",
    "tenant_preferences",
    "world_resolution_state",
}


class ObservationStorageTests(unittest.TestCase):
    def test_init_creates_product_and_observation_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(str(Path(directory) / "hosted.sqlite3"))
            with store.connection() as db:
                tables = {
                    str(row[0])
                    for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                foreign_keys = db.execute(
                    "PRAGMA foreign_key_list(friend_tags)"
                ).fetchall()

            self.assertTrue(EXPECTED_TABLES.issubset(tables))
            self.assertGreaterEqual(len(foreign_keys), 4)

    def test_new_raw_fetch_gets_stable_id_and_identical_fetches_stay_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(str(Path(directory) / "hosted.sqlite3"))
            tenant = store.bootstrap("Alice", "collector")
            for _ in range(2):
                store.record_raw_fetch(
                    tenant["tenant_id"],
                    "GET",
                    "/auth/user/friends",
                    200,
                    "application/json",
                    b"[]",
                    "",
                )
            with store.connection() as db:
                rows = db.execute(
                    """SELECT client_fetch_id FROM raw_fetches
                    WHERE tenant_id=? ORDER BY id""",
                    (tenant["tenant_id"],),
                ).fetchall()

            self.assertEqual(len(rows), 2)
            self.assertNotEqual(rows[0][0], rows[1][0])
            for row in rows:
                self.assertRegex(str(row[0]), r"^fetch_[0-9a-f]{64}$")

    def test_legacy_raw_fetch_ids_are_backfilled_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hosted.sqlite3"
            store = Store(str(path))
            tenant = store.bootstrap("Alice", "collector")
            with store.connection() as db:
                db.execute(
                    """INSERT INTO raw_fetches(
                        tenant_id,occurred_at,method,path,status_code,content_type,body,error
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        tenant["tenant_id"],
                        "2026-08-30T08:00:00+00:00",
                        "GET",
                        "/auth/user/friends",
                        200,
                        "application/json",
                        b"[]",
                        "",
                    ),
                )
                db.execute(
                    "UPDATE raw_fetches SET client_fetch_id='' WHERE tenant_id=?",
                    (tenant["tenant_id"],),
                )

            Store(str(path))
            with sqlite3.connect(path) as db:
                first = str(
                    db.execute(
                        "SELECT client_fetch_id FROM raw_fetches WHERE tenant_id=?",
                        (tenant["tenant_id"],),
                    ).fetchone()[0]
                )
            Store(str(path))
            with sqlite3.connect(path) as db:
                second = str(
                    db.execute(
                        "SELECT client_fetch_id FROM raw_fetches WHERE tenant_id=?",
                        (tenant["tenant_id"],),
                    ).fetchone()[0]
                )

            self.assertEqual(first, second)
            self.assertTrue(re.fullmatch(r"legacy_fetch_[0-9a-f]{64}", first))


if __name__ == "__main__":
    unittest.main()
