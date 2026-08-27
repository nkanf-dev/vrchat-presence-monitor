#!/usr/bin/env python3
"""Copy one local monitor SQLite database into an existing hosted tenant."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


FRIEND_COLUMNS = (
    "id", "username", "display_name", "is_self", "status",
    "status_description", "location", "platform", "avatar_url",
    "avatar_image_url", "bio", "bio_links", "last_seen", "last_changed",
    "updated_at",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        path.resolve().as_uri() + "?mode=ro&immutable=1",
        uri=True,
        timeout=30,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def event_identity(row: sqlite3.Row) -> tuple[str, str]:
    local_id = str(row["id"])
    payload = json.dumps(
        [
            local_id,
            str(row["friend_id"]),
            str(row["occurred_at"]),
            str(row["old_status"] or "unknown"),
            str(row["new_status"] or "offline"),
            str(row["location"] or ""),
            str(row["platform"] or ""),
            str(row["source"] or "local-import"),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"legacy_event_{local_id}_{hashlib.sha256(payload).hexdigest()}", f"local-{local_id}"


def migrate(source_path: Path, target_path: Path, tenant_id: str, source_name: str) -> dict[str, int]:
    result = {"friends": 0, "events": 0, "raw_fetches": 0}
    with closing(readonly(source_path)) as source, closing(sqlite3.connect(target_path, timeout=30)) as target:
        source_check = source.execute("PRAGMA integrity_check").fetchone()
        if not source_check or source_check[0] != "ok":
            raise RuntimeError(f"source integrity check failed: {source_check}")
        target.row_factory = sqlite3.Row
        target.execute("PRAGMA journal_mode=WAL")
        target.execute("PRAGMA busy_timeout=30000")
        if not target.execute("SELECT 1 FROM tenants WHERE id=?", (tenant_id,)).fetchone():
            raise RuntimeError("target tenant does not exist")
        target.execute(
            """CREATE TABLE IF NOT EXISTS local_migration_rows (
                tenant_id TEXT NOT NULL,
                source_name TEXT NOT NULL,
                entity TEXT NOT NULL,
                source_row_id INTEGER NOT NULL,
                imported_at TEXT NOT NULL,
                PRIMARY KEY(tenant_id, source_name, entity, source_row_id),
                FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
            )"""
        )

        placeholders = ",".join("?" for _ in FRIEND_COLUMNS)
        updates = ",".join(f"{column}=excluded.{column}" for column in FRIEND_COLUMNS if column != "id")
        for row in source.execute(f"SELECT {','.join(FRIEND_COLUMNS)} FROM friends ORDER BY id"):
            target.execute(
                f"""INSERT INTO friends(tenant_id,{','.join(FRIEND_COLUMNS)})
                    VALUES(?,{placeholders})
                    ON CONFLICT(tenant_id,id) DO UPDATE SET {updates}""",
                (tenant_id, *(row[column] for column in FRIEND_COLUMNS)),
            )
            result["friends"] += 1

        for row in source.execute("SELECT * FROM status_events ORDER BY id"):
            event_id, previous_id = event_identity(row)
            duplicate = target.execute(
                """SELECT 1 FROM status_events
                   WHERE tenant_id=? AND (
                       client_event_id=? OR previous_event_id=? OR client_event_id LIKE ?
                   ) LIMIT 1""",
                (tenant_id, event_id, previous_id, f"%:{previous_id}"),
            ).fetchone()
            if duplicate:
                continue
            target.execute(
                """INSERT INTO status_events(
                    tenant_id,client_event_id,friend_id,occurred_at,old_status,
                    new_status,location,platform,source,previous_event_id
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    tenant_id, event_id, row["friend_id"], row["occurred_at"],
                    row["old_status"] or "unknown", row["new_status"] or "offline",
                    row["location"] or "", row["platform"] or "",
                    row["source"] or "local-import", previous_id,
                ),
            )
            result["events"] += 1
        target.commit()

        raw_cursor = source.execute("SELECT * FROM raw_fetches ORDER BY id")
        while rows := raw_cursor.fetchmany(100):
            target.execute("BEGIN IMMEDIATE")
            for row in rows:
                local_id = int(row["id"])
                if target.execute(
                    """SELECT 1 FROM local_migration_rows
                       WHERE tenant_id=? AND source_name=? AND entity='raw_fetch' AND source_row_id=?""",
                    (tenant_id, source_name, local_id),
                ).fetchone():
                    continue
                target.execute(
                    """INSERT INTO raw_fetches(
                        tenant_id,occurred_at,method,path,status_code,content_type,body,error
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        tenant_id, row["occurred_at"], row["method"], row["path"],
                        row["status_code"], row["content_type"] or "",
                        bytes(row["body"] or b""), row["error"] or "",
                    ),
                )
                target.execute(
                    """INSERT INTO local_migration_rows(
                        tenant_id,source_name,entity,source_row_id,imported_at
                    ) VALUES(?,?,'raw_fetch',?,?)""",
                    (tenant_id, source_name, local_id, utc_now()),
                )
                result["raw_fetches"] += 1
            target.commit()

        last_sync = source.execute(
            """SELECT finished_at FROM sync_runs
               WHERE status='ok' AND finished_at IS NOT NULL
               ORDER BY finished_at DESC,id DESC LIMIT 1"""
        ).fetchone()
        if last_sync:
            target.execute(
                """UPDATE collectors SET last_sync=CASE
                    WHEN last_sync IS NULL OR last_sync < ? THEN ? ELSE last_sync END
                    WHERE tenant_id=? AND revoked_at IS NULL""",
                (last_sync["finished_at"], last_sync["finished_at"], tenant_id),
            )
            target.execute(
                """UPDATE vrchat_accounts SET last_sync=CASE
                    WHEN last_sync IS NULL OR last_sync < ? THEN ? ELSE last_sync END
                    WHERE tenant_id=?""",
                (last_sync["finished_at"], last_sync["finished_at"], tenant_id),
            )
        target.commit()
        check = target.execute("PRAGMA quick_check").fetchone()
        if not check or check[0] != "ok":
            raise RuntimeError(f"target quick check failed: {check}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--source-name", default="local-monitor")
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, args.target, args.tenant_id, args.source_name), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
