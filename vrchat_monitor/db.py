from __future__ import annotations

import csv
import copy
import base64
import io
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date as date_type, datetime, time as time_type, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .vrchat import world_id_from_location


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class Database:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self._presence_cache: dict[tuple[str, str, str, int], tuple[float, dict[str, Any]]] = {}
        self._world_presence_cache: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
        self._init()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _init(self) -> None:
        with self._lock, self._connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS friends (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    is_self INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'offline',
                    status_description TEXT NOT NULL DEFAULT '',
                    location TEXT NOT NULL DEFAULT '',
                    platform TEXT NOT NULL DEFAULT '',
                    avatar_url TEXT NOT NULL DEFAULT '',
                    avatar_image_url TEXT NOT NULL DEFAULT '',
                    bio TEXT NOT NULL DEFAULT '',
                    bio_links TEXT NOT NULL DEFAULT '[]',
                    last_seen TEXT,
                    last_changed TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS status_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    friend_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    old_status TEXT NOT NULL,
                    new_status TEXT NOT NULL,
                    location TEXT NOT NULL DEFAULT '',
                    platform TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'api',
                    FOREIGN KEY(friend_id) REFERENCES friends(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_status_events_friend_time
                    ON status_events(friend_id, occurred_at);
                CREATE INDEX IF NOT EXISTS idx_status_events_time
                    ON status_events(occurred_at);
                CREATE TABLE IF NOT EXISTS sync_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    friend_count INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS raw_fetches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    method TEXT NOT NULL,
                    path TEXT NOT NULL,
                    status_code INTEGER,
                    content_type TEXT NOT NULL DEFAULT '',
                    body BLOB NOT NULL DEFAULT X'',
                    error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_raw_fetches_time
                    ON raw_fetches(occurred_at);
                CREATE INDEX IF NOT EXISTS idx_raw_fetches_path_time
                    ON raw_fetches(path, occurred_at);
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(friends)").fetchall()}
            migrations = {
                "is_self": "INTEGER NOT NULL DEFAULT 0",
                "avatar_image_url": "TEXT NOT NULL DEFAULT ''",
                "bio": "TEXT NOT NULL DEFAULT ''",
                "bio_links": "TEXT NOT NULL DEFAULT '[]'",
            }
            for name, definition in migrations.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE friends ADD COLUMN {name} {definition}")

    def setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock, self._connection() as db:
            row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._connection() as db:
            db.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def clear_auth(self) -> None:
        with self._lock, self._connection() as db:
            db.execute("DELETE FROM settings WHERE key IN ('auth_cookie', 'auth_user')")

    def begin_sync(self, source: str) -> int:
        with self._lock, self._connection() as db:
            cur = db.execute(
                "INSERT INTO sync_runs(started_at, source, status) VALUES(?, ?, 'running')",
                (utc_now(), source),
            )
            return int(cur.lastrowid)

    def finish_sync(self, run_id: int, status: str, count: int = 0, error: str = "") -> None:
        with self._lock, self._connection() as db:
            db.execute(
                "UPDATE sync_runs SET finished_at = ?, status = ?, friend_count = ?, error = ? WHERE id = ?",
                (utc_now(), status, count, error[:500], run_id),
            )

    def record_raw_fetch(
        self,
        method: str,
        path: str,
        status_code: int | None,
        content_type: str = "",
        body: bytes = b"",
        error: str = "",
    ) -> None:
        """Keep the exact API response body for local recovery and debugging.

        Cookies and authorization headers are deliberately not part of this table.
        The VRChat API responses themselves may contain private profile/presence data,
        so this remains in the local SQLite database alongside the existing monitor data.
        """
        with self._lock, self._connection() as db:
            db.execute(
                """INSERT INTO raw_fetches
                (occurred_at, method, path, status_code, content_type, body, error)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (utc_now(), str(method or "GET"), str(path or ""), status_code,
                 str(content_type or ""), sqlite3.Binary(body or b""), str(error or "")[:500]),
            )

    def raw_fetches(self, limit: int = 50, path: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._lock, self._connection() as db:
            if path:
                rows = db.execute(
                    """SELECT id, occurred_at, method, path, status_code, content_type,
                    length(body) AS body_bytes, body, error FROM raw_fetches
                    WHERE path = ? ORDER BY id DESC LIMIT ?""", (path, limit)
                ).fetchall()
            else:
                rows = db.execute(
                    """SELECT id, occurred_at, method, path, status_code, content_type,
                    length(body) AS body_bytes, body, error FROM raw_fetches
                    ORDER BY id DESC LIMIT ?""", (limit,)
                ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            raw = item.pop("body", b"") or b""
            item["body"] = raw.decode("utf-8", errors="replace")
            result.append(item)
        return result

    def raw_fetch(self, fetch_id: int) -> dict[str, Any] | None:
        with self._lock, self._connection() as db:
            row = db.execute("SELECT * FROM raw_fetches WHERE id = ?", (fetch_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        raw = item.pop("body", b"") or b""
        item["body"] = raw.decode("utf-8", errors="replace")
        return item

    @staticmethod
    def _normalise_friend(friend: dict[str, Any], is_self: bool = False) -> dict[str, Any]:
        user_id = str(friend.get("id") or friend.get("userId") or "")
        username = str(friend.get("username") or friend.get("name") or user_id)
        display_name = str(friend.get("displayName") or username)
        raw_status = str(friend.get("status") or "").strip().lower()
        location = str(friend.get("location") or "").strip()
        status_aliases = {
            "online": "active",
            "active": "active",
            "joinme": "join me",
            "join me": "join me",
            "askme": "ask me",
            "ask me": "ask me",
            "busy": "busy",
            "mobile": "mobile",
            "website": "website",
            "offline": "offline",
        }
        status = status_aliases.get(raw_status)
        if status is None:
            status = "offline" if not location or location.lower() == "offline" else "active"
        if location.lower() == "offline":
            status = "offline"
        elif is_self and not location:
            status = "offline"
        links = friend.get("bioLinks") if "bioLinks" in friend else friend.get("bio_links") or []
        if not isinstance(links, list):
            links = [str(links)]
        return {
            "id": user_id,
            "username": username,
            "display_name": username if is_self else display_name,
            "is_self": is_self,
            "status": status,
            "status_description": str(friend.get("statusDescription") or ""),
            "location": location,
            "platform": str(friend.get("last_platform") or friend.get("platform") or ""),
            "avatar_url": str(friend.get("profilePicOverride") or friend.get("currentAvatarThumbnailImageUrl") or friend.get("avatar_url") or ""),
            "avatar_image_url": str(friend.get("currentAvatarImageUrl") or friend.get("avatar_image_url") or ""),
            "bio": str(friend.get("bio") or ""),
            "bio_links": json.dumps([str(link) for link in links], ensure_ascii=False),
        }

    def upsert_friends(self, friends: list[dict[str, Any]], source: str = "api", self_id: str | None = None) -> int:
        now = utc_now()
        changed = 0
        with self._lock, self._connection() as db:
            for raw in friends:
                friend_id = str(raw.get("id") or raw.get("userId") or "")
                friend = self._normalise_friend(raw, bool(self_id and friend_id == self_id))
                if not friend["id"]:
                    continue
                old = db.execute("SELECT * FROM friends WHERE id = ?", (friend["id"],)).fetchone()
                if old is None:
                    db.execute(
                        """INSERT INTO friends
                        (id, username, display_name, is_self, status, status_description, location, platform, avatar_url,
                         avatar_image_url, bio, bio_links,
                         last_seen, last_changed, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (friend["id"], friend["username"], friend["display_name"], int(friend["is_self"]), friend["status"],
                         friend["status_description"], friend["location"], friend["platform"], friend["avatar_url"],
                         friend["avatar_image_url"], friend["bio"], friend["bio_links"],
                         now if friend["status"] not in {"offline", ""} else None, now, now),
                    )
                    db.execute(
                        """INSERT INTO status_events
                        (friend_id, occurred_at, old_status, new_status, location, platform, source)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (friend["id"], now, "unknown", friend["status"], friend["location"], friend["platform"], source),
                    )
                    changed += 1
                    continue

                old_status = str(old["status"])
                record_is_self = bool(friend["is_self"] or old["is_self"])
                if record_is_self and (not friend["location"] or friend["location"].lower() == "offline"):
                    friend["status"] = "offline"
                stored_username = str(old["username"] or friend["username"]) if record_is_self and (not friend["username"] or friend["username"].startswith("usr_")) else friend["username"]
                status_changed = old_status != friend["status"]
                location_changed = str(old["location"] or "") != friend["location"]
                platform_changed = str(old["platform"] or "") != friend["platform"]
                last_seen = old["last_seen"]
                if friend["status"] not in {"offline", ""}:
                    last_seen = now
                elif status_changed and old_status not in {"offline", ""}:
                    last_seen = old["last_seen"] or now
                last_changed = now if status_changed else old["last_changed"]
                db.execute(
                    """UPDATE friends SET username=?, display_name=?, is_self=?, status=?, status_description=?, location=?,
                    platform=?, avatar_url=?, avatar_image_url=?, bio=?, bio_links=?, last_seen=?, last_changed=?, updated_at=? WHERE id=?""",
                    (stored_username, stored_username if record_is_self else friend["display_name"], int(record_is_self), friend["status"], friend["status_description"],
                     friend["location"], friend["platform"], friend["avatar_url"], friend["avatar_image_url"],
                     friend["bio"], friend["bio_links"], last_seen, last_changed, now, friend["id"]),
                )
                if status_changed or location_changed or platform_changed:
                    db.execute(
                        """INSERT INTO status_events
                        (friend_id, occurred_at, old_status, new_status, location, platform, source)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (friend["id"], now, old_status, friend["status"], friend["location"], friend["platform"], source),
                    )
                    changed += 1
        return changed

    def mark_missing_offline(self, seen_ids: set[str], source: str = "api") -> int:
        with self._lock, self._connection() as db:
            rows = db.execute("SELECT * FROM friends WHERE status != 'offline'").fetchall()
            now = utc_now()
            changed = 0
            for row in rows:
                if row["id"] in seen_ids:
                    continue
                db.execute("UPDATE friends SET status='offline', last_changed=?, updated_at=? WHERE id=?", (now, now, row["id"]))
                db.execute(
                    """INSERT INTO status_events
                    (friend_id, occurred_at, old_status, new_status, location, platform, source)
                    VALUES (?, ?, ?, 'offline', ?, ?, ?)""",
                    (row["id"], now, row["status"], row["location"], row["platform"], source),
                )
                changed += 1
            return changed

    def backfill_current_snapshots(self, source: str = "startup") -> int:
        """Record a current row once when an older monitor version missed its location event."""
        now = utc_now()
        inserted = 0
        with self._lock, self._connection() as db:
            rows = db.execute(
                """
                SELECT f.*, e.new_status AS event_status, e.location AS event_location, e.platform AS event_platform
                FROM friends f
                LEFT JOIN status_events e ON e.id = (
                    SELECT e2.id FROM status_events e2
                    WHERE e2.friend_id = f.id ORDER BY e2.occurred_at DESC, e2.id DESC LIMIT 1
                )
                """
            ).fetchall()
            for row in rows:
                if row["event_status"] is not None and row["event_status"] == row["status"] \
                        and str(row["event_location"] or "") == str(row["location"] or "") \
                        and str(row["event_platform"] or "") == str(row["platform"] or ""):
                    continue
                db.execute(
                    """INSERT INTO status_events
                    (friend_id, occurred_at, old_status, new_status, location, platform, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (row["id"], now, str(row["event_status"] or "unknown"), row["status"], row["location"], row["platform"], source),
                )
                inserted += 1
        return inserted

    def friends(self) -> list[dict[str, Any]]:
        with self._lock, self._connection() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT * FROM friends ORDER BY is_self DESC, CASE WHEN status = 'offline' THEN 1 ELSE 0 END, display_name COLLATE NOCASE"
            ).fetchall()]
            for row in rows:
                try:
                    row["bio_links"] = json.loads(row.get("bio_links") or "[]")
                except (TypeError, json.JSONDecodeError):
                    row["bio_links"] = []
            return rows

    def recent_events(self, limit: int = 25) -> list[dict[str, Any]]:
        with self._lock, self._connection() as db:
            return [dict(row) for row in db.execute(
                """SELECT e.*, f.display_name, f.is_self FROM status_events e JOIN friends f ON f.id=e.friend_id
                ORDER BY e.occurred_at DESC LIMIT ?""", (max(1, min(limit, 200)),)
            ).fetchall()]

    def history_page(self, offset: int = 0, limit: int = 25, query: str = "") -> dict[str, Any]:
        """Return a searchable, deterministic page of the complete event history."""
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), 100))
        text = str(query or "").strip().lower()
        where = ""
        params: list[Any] = []
        if text:
            like = f"%{text}%"
            where = "WHERE lower(f.display_name) LIKE ? OR lower(f.username) LIKE ? " \
                    "OR lower(e.old_status) LIKE ? OR lower(e.new_status) LIKE ? " \
                    "OR lower(e.location) LIKE ? OR lower(e.platform) LIKE ? OR lower(e.source) LIKE ?"
            params = [like] * 7
        with self._lock, self._connection() as db:
            total = int(db.execute(
                f"SELECT COUNT(*) FROM status_events e JOIN friends f ON f.id=e.friend_id {where}", params
            ).fetchone()[0])
            rows = db.execute(
                f"""SELECT e.*, f.display_name, f.username, f.is_self
                FROM status_events e JOIN friends f ON f.id=e.friend_id {where}
                ORDER BY e.occurred_at DESC, e.id DESC LIMIT ? OFFSET ?""",
                [*params, limit, offset],
            ).fetchall()
        items = [dict(row) for row in rows]
        return {
            "items": items,
            "offset": offset,
            "limit": limit,
            "total": total,
            "has_more": offset + len(items) < total,
        }

    def events_after(self, event_id: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 5000))
        with self._lock, self._connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM status_events WHERE id > ? ORDER BY id LIMIT ?",
                (max(0, int(event_id)), limit),
            ).fetchall()]

    def _online_seconds(self, friend_id: str, start: datetime, end: datetime) -> float:
        with self._lock, self._connection() as db:
            events = [dict(row) for row in db.execute(
                "SELECT occurred_at, old_status, new_status FROM status_events WHERE friend_id=? AND occurred_at <= ? ORDER BY occurred_at",
                (friend_id, end.isoformat(timespec="seconds")),
            ).fetchall()]
            current = "offline"
            cursor = start
            seconds = 0.0
            for event in events:
                at = parse_time(event["occurred_at"])
                if at is None:
                    continue
                if at < start:
                    current = event["new_status"]
                    continue
                if at > end:
                    break
                if current != "offline":
                    seconds += max(0.0, (at - cursor).total_seconds())
                current = event["new_status"]
                cursor = at
            if current != "offline":
                seconds += max(0.0, (end - cursor).total_seconds())
            return seconds

    def stats(self, days: int = 7) -> dict[str, Any]:
        days = max(1, min(days, 90))
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        friend_rows = self.friends()
        online = [row for row in friend_rows if row["status"] != "offline"]
        totals = []
        for row in friend_rows:
            seconds = self._online_seconds(row["id"], start, end)
            totals.append({"id": row["id"], "name": row["display_name"], "seconds": round(seconds), "hours": round(seconds / 3600, 1)})
        with self._lock, self._connection() as db:
            status_counts = {row["new_status"]: row["count"] for row in db.execute(
                """SELECT new_status, COUNT(*) AS count FROM status_events
                WHERE occurred_at >= ? GROUP BY new_status""", (start.isoformat(timespec="seconds"),)
            ).fetchall()}
            daily = [dict(row) for row in db.execute(
                """SELECT substr(occurred_at, 1, 10) AS day, COUNT(*) AS changes
                FROM status_events WHERE occurred_at >= ? GROUP BY day ORDER BY day""",
                (start.isoformat(timespec="seconds"),),
            ).fetchall()]
        return {
            "days": days,
            "online_now": len(online),
            "friend_count": len(friend_rows),
            "status_counts": status_counts,
            "daily_changes": daily,
            "online_hours": sorted(totals, key=lambda item: item["seconds"], reverse=True)[:10],
            "online_hours_all": sorted(totals, key=lambda item: item["seconds"], reverse=True),
        }

    def csv_export(self) -> str:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["friend_id", "display_name", "occurred_at", "old_status", "new_status", "location", "platform", "source"])
        with self._lock, self._connection() as db:
            rows = db.execute(
                """SELECT e.friend_id, f.display_name, e.occurred_at, e.old_status, e.new_status,
                e.location, e.platform, e.source FROM status_events e JOIN friends f ON f.id=e.friend_id ORDER BY e.occurred_at"""
            ).fetchall()
            writer.writerows([tuple(row) for row in rows])
        return output.getvalue()

    def json_export(self) -> dict[str, Any]:
        """Export recoverable local data without settings or authentication material."""
        with self._lock, self._connection() as db:
            friends = [dict(row) for row in db.execute("SELECT * FROM friends ORDER BY id").fetchall()]
            events = [dict(row) for row in db.execute("SELECT * FROM status_events ORDER BY id").fetchall()]
            sync_runs = [dict(row) for row in db.execute("SELECT * FROM sync_runs ORDER BY id").fetchall()]
            raw_fetches = []
            for row in db.execute("SELECT * FROM raw_fetches ORDER BY id").fetchall():
                item = dict(row)
                body = item.pop("body", b"") or b""
                item["body_b64"] = base64.b64encode(body).decode("ascii")
                raw_fetches.append(item)
        return {
            "format": "vrchat-monitor-backup",
            "version": 1,
            "exported_at": utc_now(),
            "friends": friends,
            "status_events": events,
            "sync_runs": sync_runs,
            "raw_fetches": raw_fetches,
        }

    def json_import(self, payload: dict[str, Any]) -> dict[str, int]:
        """Merge a backup. It never deletes rows and never imports settings/cookies."""
        if not isinstance(payload, dict) or payload.get("format") != "vrchat-monitor-backup":
            raise ValueError("不是有效的 VRChat Monitor 备份文件")
        if int(payload.get("version", 0)) != 1:
            raise ValueError("不支持的备份版本")
        imported = {"friends": 0, "status_events": 0, "sync_runs": 0, "raw_fetches": 0}
        friends = payload.get("friends") if isinstance(payload.get("friends"), list) else []
        events = payload.get("status_events") if isinstance(payload.get("status_events"), list) else []
        sync_runs = payload.get("sync_runs") if isinstance(payload.get("sync_runs"), list) else []
        raw_fetches = payload.get("raw_fetches") if isinstance(payload.get("raw_fetches"), list) else []
        with self._lock, self._connection() as db:
            for item in friends:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                friend_id = str(item["id"])
                exists = db.execute("SELECT 1 FROM friends WHERE id=?", (friend_id,)).fetchone()
                values = (
                    str(item.get("username") or friend_id), str(item.get("display_name") or item.get("displayName") or friend_id),
                    int(bool(item.get("is_self"))), str(item.get("status") or "offline"),
                    str(item.get("status_description") or ""), str(item.get("location") or ""),
                    str(item.get("platform") or ""), str(item.get("avatar_url") or ""),
                    str(item.get("avatar_image_url") or ""), str(item.get("bio") or ""),
                    json.dumps(item.get("bio_links") if isinstance(item.get("bio_links"), list) else [], ensure_ascii=False),
                    item.get("last_seen"), item.get("last_changed"), item.get("updated_at") or utc_now(), friend_id,
                )
                if exists:
                    db.execute(
                        """UPDATE friends SET username=?, display_name=?, is_self=?, status=?, status_description=?,
                        location=?, platform=?, avatar_url=?, avatar_image_url=?, bio=?, bio_links=?, last_seen=?, last_changed=?, updated_at=?
                        WHERE id=?""", values,
                    )
                else:
                    db.execute(
                        """INSERT INTO friends (username, display_name, is_self, status, status_description, location,
                        platform, avatar_url, avatar_image_url, bio, bio_links, last_seen, last_changed, updated_at, id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", values,
                    )
                imported["friends"] += 1
            for item in events:
                if not isinstance(item, dict) or not item.get("friend_id") or not item.get("occurred_at"):
                    continue
                values = (
                    str(item["friend_id"]), str(item["occurred_at"]), str(item.get("old_status") or "unknown"),
                    str(item.get("new_status") or "offline"), str(item.get("location") or ""),
                    str(item.get("platform") or ""), str(item.get("source") or "import"),
                )
                cursor = db.execute(
                    """INSERT INTO status_events (friend_id, occurred_at, old_status, new_status, location, platform, source)
                    SELECT ?, ?, ?, ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM friends WHERE id=?) AND NOT EXISTS (
                        SELECT 1 FROM status_events WHERE friend_id=? AND occurred_at=? AND old_status=? AND new_status=?
                        AND location=? AND platform=? AND source=?)""",
                    (*values, values[0], *values),
                )
                imported["status_events"] += int(cursor.rowcount > 0)
            for item in sync_runs:
                if not isinstance(item, dict) or not item.get("started_at"):
                    continue
                values = (str(item.get("started_at")), item.get("finished_at"), str(item.get("source") or "import"),
                          str(item.get("status") or "unknown"), item.get("friend_count"), item.get("error"))
                exists = db.execute("SELECT 1 FROM sync_runs WHERE started_at=? AND source=? AND status=?", (values[0], values[2], values[3])).fetchone()
                if not exists:
                    db.execute("INSERT INTO sync_runs (started_at, finished_at, source, status, friend_count, error) VALUES (?, ?, ?, ?, ?, ?)", values)
                    imported["sync_runs"] += 1
            for item in raw_fetches:
                if not isinstance(item, dict) or not item.get("occurred_at"):
                    continue
                try:
                    body = base64.b64decode(str(item.get("body_b64") or ""), validate=True)
                except (ValueError, base64.binascii.Error):
                    continue
                if len(body) > 8 * 1024 * 1024:
                    continue
                values = (str(item.get("occurred_at")), str(item.get("method") or "GET"), str(item.get("path") or ""),
                          item.get("status_code"), str(item.get("content_type") or ""), body, str(item.get("error") or ""))
                exists = db.execute("SELECT 1 FROM raw_fetches WHERE occurred_at=? AND method=? AND path=? AND status_code=?", values[:4]).fetchone()
                if not exists:
                    db.execute("INSERT INTO raw_fetches (occurred_at, method, path, status_code, content_type, body, error) VALUES (?, ?, ?, ?, ?, ?, ?)", values)
                    imported["raw_fetches"] += 1
        return imported

    def last_sync(self) -> dict[str, Any] | None:
        with self._lock, self._connection() as db:
            row = db.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()
            return dict(row) if row else None

    @staticmethod
    def _local_zone() -> timezone | ZoneInfo:
        current = datetime.now().astimezone()
        key = getattr(current.tzinfo, "key", None)
        if key:
            try:
                return ZoneInfo(key)
            except Exception:
                pass
        return current.tzinfo or timezone.utc

    @staticmethod
    def _day_bounds(day: date_type, zone: timezone | ZoneInfo, now: datetime) -> tuple[datetime, datetime]:
        start = datetime.combine(day, time_type.min, tzinfo=zone)
        end = datetime.combine(day + timedelta(days=1), time_type.min, tzinfo=zone)
        if day == now.date():
            end = min(end, now)
        return start, end

    @staticmethod
    def _effective_presence_state(status: str, location: str, is_self: bool = False) -> str:
        if location.strip().lower() == "offline":
            return "offline"
        if is_self and not location.strip():
            return "offline"
        return status

    @classmethod
    def _online_spans(cls, events: list[sqlite3.Row], start: datetime, end: datetime, zone: timezone | ZoneInfo, is_self: bool = False) -> list[dict[str, Any]]:
        if end <= start:
            return []
        state = "offline"
        cursor = start
        spans: list[dict[str, Any]] = []
        for event in events:
            occurred = parse_time(event["occurred_at"])
            if occurred is None:
                continue
            occurred = occurred.astimezone(zone)
            new_state = cls._effective_presence_state(str(event["new_status"] or "offline"), str(event["location"] or ""), is_self)
            if occurred < start:
                state = new_state
                continue
            if occurred >= end:
                break
            if state != "offline" and occurred > cursor:
                spans.append({
                    "start_minute": round((cursor - start).total_seconds() / 60),
                    "end_minute": round((occurred - start).total_seconds() / 60),
                    "status": state,
                })
            state = new_state
            cursor = max(cursor, occurred)
        if state != "offline" and end > cursor:
            spans.append({
                "start_minute": round((cursor - start).total_seconds() / 60),
                "end_minute": round((end - start).total_seconds() / 60),
                "status": state,
            })
        return [span for span in spans if span["end_minute"] > span["start_minute"]]

    def presence_overview(
        self,
        day: str | None = None,
        days: int = 30,
        heatmap_from: str | None = None,
        heatmap_to: str | None = None,
    ) -> dict[str, Any]:
        zone = self._local_zone()
        now = datetime.now(zone)
        today = now.date()

        def parse_date(value: str | None, label: str) -> date_type | None:
            if not value:
                return None
            try:
                return datetime.strptime(value, "%Y-%m-%d").date()
            except ValueError as error:
                raise ValueError(f"{label}必须是 YYYY-MM-DD") from error

        selected = parse_date(day, "日期") or today
        future_clamped = selected > today
        selected = min(selected, today)

        try:
            legacy_days = max(7, min(int(days), 90))
        except (TypeError, ValueError):
            legacy_days = 30
        range_start = parse_date(heatmap_from, "热力图起始日期")
        range_end = parse_date(heatmap_to, "热力图结束日期")
        if range_end is None:
            range_end = today
        if range_start is None:
            range_start = range_end - timedelta(days=legacy_days - 1)
        if range_start > range_end:
            raise ValueError("热力图起始日期不能晚于结束日期")
        if range_start > today or range_end > today:
            future_clamped = True
            range_start = min(range_start, today)
            range_end = min(range_end, today)
            if range_start > range_end:
                range_start = range_end
        range_days = (range_end - range_start).days + 1
        if range_days > 730:
            raise ValueError("热力图范围不能超过 730 天")

        query_start_day = min(selected, range_start)
        query_end_day = max(selected, range_end)
        window_start = datetime.combine(query_start_day, time_type.min, tzinfo=zone)
        window_end = self._day_bounds(query_end_day, zone, now)[1]
        start_utc = window_start.astimezone(timezone.utc).isoformat(timespec="seconds")
        end_utc = window_end.astimezone(timezone.utc).isoformat(timespec="seconds")
        heatmap_from_key = range_start.isoformat()
        heatmap_to_key = range_end.isoformat()

        with self._lock, self._connection() as db:
            event_revision = int(db.execute("SELECT COALESCE(MAX(id), 0) FROM status_events").fetchone()[0])
            cache_key = (selected.isoformat(), heatmap_from_key, heatmap_to_key, event_revision)
            cached = self._presence_cache.get(cache_key)
            cache_is_stable = selected < today and range_end < today
            if cached and (cache_is_stable or time.monotonic() - cached[0] < 15):
                return copy.deepcopy(cached[1])
            friend_rows = [dict(row) for row in db.execute(
                "SELECT id, username, display_name, is_self FROM friends ORDER BY is_self DESC, CASE WHEN status = 'offline' THEN 1 ELSE 0 END, display_name COLLATE NOCASE"
            ).fetchall()]
            event_rows = db.execute(
                """
                WITH prior AS (
                    SELECT e.id
                    FROM status_events e
                    WHERE e.occurred_at < ?
                      AND e.id = (
                          SELECT e2.id FROM status_events e2
                          WHERE e2.friend_id = e.friend_id AND e2.occurred_at < ?
                          ORDER BY e2.occurred_at DESC, e2.id DESC LIMIT 1
                      )
                )
                SELECT e.* FROM status_events e
                WHERE e.occurred_at >= ? AND e.occurred_at < ?
                UNION ALL
                SELECT e.* FROM status_events e WHERE e.id IN prior
                ORDER BY friend_id, occurred_at, id
                """,
                (start_utc, start_utc, start_utc, end_utc),
            ).fetchall()

        events_by_friend: dict[str, list[sqlite3.Row]] = {}
        for event in event_rows:
            events_by_friend.setdefault(str(event["friend_id"]), []).append(event)

        day_start, day_end = self._day_bounds(selected, zone, now)
        timeline = []
        heatmap = []
        completed_days = max(0, range_days - (1 if range_start <= today <= range_end else 0))
        for friend in friend_rows:
            friend_id = str(friend["id"])
            events = events_by_friend.get(friend_id, [])
            selected_spans = self._online_spans(events, day_start, day_end, zone, bool(friend["is_self"]))
            selected_minutes = round(sum(span["end_minute"] - span["start_minute"] for span in selected_spans), 1)
            timeline.append({
                "id": friend_id,
                "name": friend["display_name"],
                "username": friend["username"],
                "is_self": bool(friend["is_self"]),
                "spans": selected_spans,
                "online_minutes": selected_minutes,
            })

            total_minutes = [0.0] * 24
            for offset in range(range_days):
                current_day = range_start + timedelta(days=offset)
                if current_day == today:
                    continue
                current_start, current_end = self._day_bounds(current_day, zone, now)
                spans = self._online_spans(events, current_start, current_end, zone, bool(friend["is_self"]))
                for hour in range(24):
                    hour_start = hour * 60
                    hour_end = (hour + 1) * 60
                    for span in spans:
                        total_minutes[hour] += max(0, min(span["end_minute"], hour_end) - max(span["start_minute"], hour_start))
            heatmap.append({
                "id": friend_id,
                "name": friend["display_name"],
                "values": [round(total_minutes[hour] / (max(1, completed_days) * 60), 3) for hour in range(24)],
            })

        result = {
            "day": selected.isoformat(),
            "days": range_days,
            "future_clamped": future_clamped,
            "heatmap_from": heatmap_from_key,
            "heatmap_to": heatmap_to_key,
            "heatmap_days": completed_days,
            "timezone": getattr(zone, "key", str(zone)),
            "timeline": timeline,
            "heatmap": heatmap,
        }
        with self._lock:
            if len(self._presence_cache) > 64:
                self._presence_cache.clear()
            self._presence_cache[cache_key] = (time.monotonic(), result)
        return copy.deepcopy(result)

    @staticmethod
    def _world_spans(events: list[sqlite3.Row], start: datetime, end: datetime, zone: timezone | ZoneInfo, is_self: bool = False) -> list[dict[str, Any]]:
        if end <= start:
            return []
        state = "offline"
        location = ""
        platform = ""
        cursor = start
        spans: list[dict[str, Any]] = []
        for event in events:
            occurred = parse_time(event["occurred_at"])
            if occurred is None:
                continue
            occurred = occurred.astimezone(zone)
            new_state = Database._effective_presence_state(str(event["new_status"] or "offline"), str(event["location"] or ""), is_self)
            event_location = str(event["location"] or "")
            event_platform = str(event["platform"] or "")
            if occurred < start:
                state = new_state
                if event_location:
                    location = event_location
                if event_platform:
                    platform = event_platform
                continue
            if occurred >= end:
                break
            if state != "offline" and occurred > cursor:
                world_id = world_id_from_location(location)
                spans.append({
                    "start_minute": round((cursor - start).total_seconds() / 60),
                    "end_minute": round((occurred - start).total_seconds() / 60),
                    "status": state,
                    "location": location,
                    "world_id": world_id or "",
                    "platform": platform,
                })
            state = new_state
            if event_location:
                location = event_location
            if event_platform:
                platform = event_platform
            cursor = max(cursor, occurred)
        if state != "offline" and end > cursor:
            world_id = world_id_from_location(location)
            spans.append({
                "start_minute": round((cursor - start).total_seconds() / 60),
                "end_minute": round((end - cursor).total_seconds() / 60 + (cursor - start).total_seconds() / 60),
                "status": state,
                "location": location,
                "world_id": world_id or "",
                "platform": platform,
            })
        return [span for span in spans if span["end_minute"] > span["start_minute"]]

    def world_presence_overview(self, day: str | None = None) -> dict[str, Any]:
        zone = self._local_zone()
        now = datetime.now(zone)
        today = now.date()
        if day:
            try:
                selected = datetime.strptime(day, "%Y-%m-%d").date()
            except ValueError as error:
                raise ValueError("日期必须是 YYYY-MM-DD") from error
        else:
            selected = today
        future_clamped = selected > today
        selected = min(selected, today)
        day_start, day_end = self._day_bounds(selected, zone, now)
        start_utc = day_start.astimezone(timezone.utc).isoformat(timespec="seconds")
        end_utc = day_end.astimezone(timezone.utc).isoformat(timespec="seconds")
        with self._lock, self._connection() as db:
            event_revision = int(db.execute("SELECT COALESCE(MAX(id), 0) FROM status_events").fetchone()[0])
            cache_key = (selected.isoformat(), event_revision)
            cached = self._world_presence_cache.get(cache_key)
            cache_is_stable = selected < today
            if cached and (cache_is_stable or time.monotonic() - cached[0] < 15):
                result = copy.deepcopy(cached[1])
                result["future_clamped"] = future_clamped
                return result
            friend_rows = [dict(row) for row in db.execute(
                "SELECT id, username, display_name, is_self, avatar_url FROM friends "
                "ORDER BY is_self DESC, CASE WHEN status = 'offline' THEN 1 ELSE 0 END, display_name COLLATE NOCASE"
            ).fetchall()]
            event_rows = db.execute(
                """
                WITH prior AS (
                    SELECT e.id
                    FROM status_events e
                    WHERE e.occurred_at < ?
                      AND e.id = (
                          SELECT e2.id FROM status_events e2
                          WHERE e2.friend_id = e.friend_id AND e2.occurred_at < ?
                          ORDER BY e2.occurred_at DESC, e2.id DESC LIMIT 1
                      )
                )
                SELECT e.* FROM status_events e
                WHERE e.occurred_at >= ? AND e.occurred_at < ?
                UNION ALL
                SELECT e.* FROM status_events e WHERE e.id IN prior
                ORDER BY friend_id, occurred_at, id
                """,
                (start_utc, start_utc, start_utc, end_utc),
            ).fetchall()
        events_by_friend: dict[str, list[sqlite3.Row]] = {}
        for event in event_rows:
            events_by_friend.setdefault(str(event["friend_id"]), []).append(event)
        rows: list[dict[str, Any]] = []
        world_ids: set[str] = set()
        for friend in friend_rows:
            spans = self._world_spans(events_by_friend.get(str(friend["id"]), []), day_start, day_end, zone, bool(friend["is_self"]))
            for span in spans:
                if span["world_id"]:
                    world_ids.add(span["world_id"])
            rows.append({
                "id": str(friend["id"]),
                "name": friend["display_name"],
                "username": friend["username"],
                "is_self": bool(friend["is_self"]),
                "avatar_url": friend["avatar_url"],
                "online_minutes": round(sum(span["end_minute"] - span["start_minute"] for span in spans), 1),
                "spans": spans,
            })
        result = {
            "day": selected.isoformat(),
            "future_clamped": future_clamped,
            "timezone": getattr(zone, "key", str(zone)),
            "self_id": next((row["id"] for row in rows if row["is_self"]), ""),
            "friends": rows,
            "world_ids": sorted(world_ids),
        }
        with self._lock:
            if len(self._world_presence_cache) > 64:
                self._world_presence_cache.clear()
            self._world_presence_cache[(selected.isoformat(), event_revision)] = (time.monotonic(), result)
        return copy.deepcopy(result)
