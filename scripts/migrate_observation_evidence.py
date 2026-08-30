from __future__ import annotations

import hashlib
import json
import sqlite3
import argparse
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


FRIEND_PATH_ONLINE = "/auth/user/friends?offset=0&n=100&offline=false"
FRIEND_PATH_OFFLINE = "/auth/user/friends?offset=0&n=100&offline=true"
AUTH_USER_PATH = "/auth/user"
SNAPSHOT_PATHS = {AUTH_USER_PATH, FRIEND_PATH_ONLINE, FRIEND_PATH_OFFLINE}


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _stable_id(prefix: str, *parts: object) -> str:
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()
    return f"{prefix}_{hashlib.sha256(payload).hexdigest()}"


def _error_category(error: str) -> str:
    folded = str(error or "").casefold()
    if "401" in folded or "missing credentials" in folded:
        return "session_expired"
    if any(token in folded for token in ("429", "rate limit", "too many requests")):
        return "rate_limited"
    if any(token in folded for token in ("ssl", "urlopen", "timeout", "network", "eof")):
        return "network"
    return "unknown" if folded else ""


def _json_body(row: sqlite3.Row) -> object | None:
    try:
        return json.loads(bytes(row["body"] or b"").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _snapshot_ids(group: list[sqlite3.Row]) -> tuple[set[str], int] | None:
    latest: dict[str, sqlite3.Row] = {}
    for row in group:
        latest[str(row["path"])] = row
    if not SNAPSHOT_PATHS.issubset(latest):
        return None
    self_payload = _json_body(latest[AUTH_USER_PATH])
    online_payload = _json_body(latest[FRIEND_PATH_ONLINE])
    offline_payload = _json_body(latest[FRIEND_PATH_OFFLINE])
    if not isinstance(self_payload, dict) or not isinstance(online_payload, list) or not isinstance(offline_payload, list):
        return None
    ids: set[str] = set()
    online_ids: set[str] = set()
    self_id = str(self_payload.get("id") or self_payload.get("userId") or "")
    if self_id:
        ids.add(self_id)
        if str(self_payload.get("status") or "").casefold() != "offline" and str(self_payload.get("location") or "").casefold() != "offline":
            online_ids.add(self_id)
    for payload, online in ((online_payload, True), (offline_payload, False)):
        for item in payload:
            if not isinstance(item, dict):
                continue
            friend_id = str(item.get("id") or item.get("userId") or "")
            if not friend_id:
                continue
            ids.add(friend_id)
            if online:
                online_ids.add(friend_id)
    return ids, len(online_ids)


def _raw_snapshots(target: sqlite3.Connection, tenant_id: str) -> list[tuple[datetime, set[str], int, tuple[str, ...]]]:
    rows = target.execute(
        """SELECT id,client_fetch_id,occurred_at,path,body FROM raw_fetches
           WHERE tenant_id=? AND status_code=200
             AND path IN (?,?,?) ORDER BY occurred_at,id""",
        (tenant_id, AUTH_USER_PATH, FRIEND_PATH_ONLINE, FRIEND_PATH_OFFLINE),
    ).fetchall()
    groups: list[list[sqlite3.Row]] = []
    current: list[sqlite3.Row] = []
    previous: datetime | None = None
    for row in rows:
        occurred = _timestamp(row["occurred_at"])
        if occurred is None:
            continue
        if previous is not None and (occurred - previous).total_seconds() > 45:
            if current:
                groups.append(current)
            current = []
        current.append(row)
        previous = occurred
    if current:
        groups.append(current)

    snapshots: list[tuple[datetime, set[str], int, tuple[str, ...]]] = []
    for group in groups:
        result = _snapshot_ids(group)
        if result is None:
            continue
        ids, online_count = result
        observed = max(filter(None, (_timestamp(row["occurred_at"]) for row in group)))
        fetch_ids = tuple(sorted(str(row["client_fetch_id"] or row["id"]) for row in group))
        snapshots.append((observed, ids, online_count, fetch_ids))
    return snapshots


def _insert_tracking(
    target: sqlite3.Connection,
    tenant_id: str,
    friend_id: str,
    tracked: bool,
    occurred_at: datetime,
    source: str,
) -> int:
    event_id = _stable_id("tracking_legacy", tenant_id, friend_id, tracked, _iso(occurred_at), source)
    cursor = target.execute(
        """INSERT OR IGNORE INTO friend_tracking_events(
               tenant_id,event_id,friend_id,tracked,occurred_at,source
           ) VALUES(?,?,?,?,?,?)""",
        (tenant_id, event_id, friend_id, int(tracked), _iso(occurred_at), source),
    )
    return int(cursor.rowcount > 0)


def migrate_observation_evidence(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    tenant_id: str,
    source_name: str,
    *,
    expected_interval_seconds: int = 180,
) -> dict[str, int]:
    """Materialize legacy local history into the current observation schema.

    Every generated identity is deterministic. Re-running this migration is safe,
    and the source status-event rows are never updated or deleted.
    """
    result = {"collection_samples": 0, "friend_tracking_events": 0}
    target.row_factory = sqlite3.Row
    source.row_factory = sqlite3.Row

    target.execute("BEGIN IMMEDIATE")
    try:
        has_sync_runs = source.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_runs'"
        ).fetchone() is not None
        if has_sync_runs:
            for row in source.execute("SELECT * FROM sync_runs ORDER BY id"):
                observed = _timestamp(row["finished_at"] or row["started_at"])
                if observed is None or str(row["status"]) == "running":
                    continue
                started = _timestamp(row["started_at"])
                duration_ms = None
                if started is not None:
                    duration_ms = max(0, round((observed - started).total_seconds() * 1000))
                success = str(row["status"]) == "ok"
                sample_id = _stable_id("sample_legacy_sync", source_name, int(row["id"]))
                cursor = target.execute(
                    """INSERT OR IGNORE INTO collection_samples(
                           tenant_id,sample_id,observed_at,source,outcome,authoritative,
                           expected_interval_seconds,friend_count,online_count,duration_ms,error_category
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        tenant_id,
                        sample_id,
                        _iso(observed),
                        "legacy-local-sync",
                        "success" if success else "failure",
                        1,
                        expected_interval_seconds,
                        int(row["friend_count"]) if success else None,
                        None,
                        duration_ms,
                        "" if success else _error_category(str(row["error"] or "")),
                    ),
                )
                result["collection_samples"] += int(cursor.rowcount > 0)

        snapshots = _raw_snapshots(target, tenant_id)
        existing_successes = [
            stamp
            for row in target.execute(
                """SELECT observed_at FROM collection_samples
                   WHERE tenant_id=? AND authoritative=1 AND outcome='success'""",
                (tenant_id,),
            )
            if (stamp := _timestamp(row["observed_at"])) is not None
        ]
        for observed, ids, online_count, fetch_ids in snapshots:
            if any(abs((observed - stamp).total_seconds()) <= 45 for stamp in existing_successes):
                continue
            sample_id = _stable_id("sample_legacy_raw", tenant_id, *fetch_ids)
            cursor = target.execute(
                """INSERT OR IGNORE INTO collection_samples(
                       tenant_id,sample_id,observed_at,source,outcome,authoritative,
                       expected_interval_seconds,friend_count,online_count,duration_ms,error_category
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    tenant_id,
                    sample_id,
                    _iso(observed),
                    "legacy-raw-snapshot",
                    "success",
                    1,
                    expected_interval_seconds,
                    len(ids),
                    online_count,
                    None,
                    "",
                ),
            )
            if cursor.rowcount > 0:
                result["collection_samples"] += 1
                existing_successes.append(observed)

        first_events = [
            (stamp, str(row["friend_id"]))
            for row in target.execute(
                """SELECT friend_id,MIN(occurred_at) AS occurred_at
                   FROM status_events WHERE tenant_id=? GROUP BY friend_id""",
                (tenant_id,),
            )
            if (stamp := _timestamp(row["occurred_at"])) is not None
        ]
        timeline: list[tuple[datetime, int, object]] = [
            (stamp, 0, friend_id) for stamp, friend_id in first_events
        ]
        timeline.extend((stamp, 1, ids) for stamp, ids, _online, _fetches in snapshots)
        tracked: set[str] = set()
        for occurred, kind, value in sorted(timeline, key=lambda item: (item[0], item[1])):
            if kind == 0:
                friend_id = str(value)
                if friend_id not in tracked:
                    result["friend_tracking_events"] += _insert_tracking(
                        target, tenant_id, friend_id, True, occurred, "legacy-status-history"
                    )
                    tracked.add(friend_id)
                continue
            snapshot_ids = set(value)  # type: ignore[arg-type]
            for friend_id in sorted(tracked - snapshot_ids):
                result["friend_tracking_events"] += _insert_tracking(
                    target, tenant_id, friend_id, False, occurred, "legacy-raw-snapshot"
                )
            for friend_id in sorted(snapshot_ids - tracked):
                result["friend_tracking_events"] += _insert_tracking(
                    target, tenant_id, friend_id, True, occurred, "legacy-raw-snapshot"
                )
            tracked = snapshot_ids

        target.execute(
            """INSERT INTO schema_meta(key,value) VALUES(?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (f"legacy_observation_evidence:{tenant_id}:{source_name}", _iso(datetime.now(timezone.utc))),
        )
        target.commit()
    except Exception:
        target.rollback()
        raise
    return result


def _readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        path.resolve().as_uri() + "?mode=ro&immutable=1",
        uri=True,
        timeout=30,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate legacy observation evidence")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--source-name", default="local-monitor")
    args = parser.parse_args()
    with closing(_readonly(args.source)) as source, closing(
        sqlite3.connect(args.target, timeout=30)
    ) as target:
        target.row_factory = sqlite3.Row
        target.execute("PRAGMA journal_mode=WAL")
        target.execute("PRAGMA busy_timeout=30000")
        result = migrate_observation_evidence(
            source, target, args.tenant_id, args.source_name
        )
        check = target.execute("PRAGMA quick_check").fetchone()
        if not check or check[0] != "ok":
            raise RuntimeError(f"target quick check failed: {check}")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
