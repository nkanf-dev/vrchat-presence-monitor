from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_timestamp(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str, *, friend_limit: int = 10_000, event_limit: int = 1_000_000):
        self.path = path
        self.friend_limit = max(1, int(friend_limit))
        self.event_limit = max(1, int(event_limit))
        self.lock = threading.RLock()
        self._init()

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _init(self):
        with self.lock, self.connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS tenants (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS collectors (
                id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL, revoked_at TEXT, last_sync TEXT, last_error TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS viewer_tokens (
                id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL, expires_at TEXT, revoked_at TEXT, FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS login_codes (
                id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, code_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL, revoked_at TEXT,
                FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS security_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL, actor_kind TEXT NOT NULL, actor_id TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL, target_kind TEXT NOT NULL, target_id TEXT NOT NULL DEFAULT '',
                details_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_security_audit_tenant_time
                ON security_audit(tenant_id, occurred_at DESC, id DESC);
            CREATE TABLE IF NOT EXISTS friends (
                tenant_id TEXT NOT NULL, id TEXT NOT NULL, username TEXT NOT NULL DEFAULT '', display_name TEXT NOT NULL DEFAULT '',
                is_self INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'offline', status_description TEXT NOT NULL DEFAULT '',
                location TEXT NOT NULL DEFAULT '', platform TEXT NOT NULL DEFAULT '', avatar_url TEXT NOT NULL DEFAULT '',
                avatar_image_url TEXT NOT NULL DEFAULT '', bio TEXT NOT NULL DEFAULT '', bio_links TEXT NOT NULL DEFAULT '[]',
                last_seen TEXT, last_changed TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(tenant_id, id),
                FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS status_events (
                tenant_id TEXT NOT NULL, client_event_id TEXT NOT NULL, friend_id TEXT NOT NULL, occurred_at TEXT NOT NULL,
                old_status TEXT NOT NULL, new_status TEXT NOT NULL, location TEXT NOT NULL DEFAULT '', platform TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'server-api', PRIMARY KEY(tenant_id, client_event_id),
                FOREIGN KEY(tenant_id, friend_id) REFERENCES friends(tenant_id, id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_hosted_events_tenant_time ON status_events(tenant_id, occurred_at);
            CREATE TABLE IF NOT EXISTS raw_fetches (
                id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, occurred_at TEXT NOT NULL, method TEXT NOT NULL,
                path TEXT NOT NULL, status_code INTEGER, content_type TEXT NOT NULL DEFAULT '', body BLOB NOT NULL DEFAULT X'', error TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
            );
            """)
            viewer_columns = {row["name"] for row in db.execute("PRAGMA table_info(viewer_tokens)").fetchall()}
            if "expires_at" not in viewer_columns:
                db.execute("ALTER TABLE viewer_tokens ADD COLUMN expires_at TEXT")
            for row in db.execute("SELECT tenant_id, id, updated_at FROM friends").fetchall():
                normalized = normalize_timestamp(row["updated_at"], "1970-01-01T00:00:00+00:00")
                if normalized != row["updated_at"]:
                    db.execute("UPDATE friends SET updated_at=? WHERE tenant_id=? AND id=?", (normalized, row["tenant_id"], row["id"]))
            for row in db.execute("SELECT tenant_id, client_event_id, occurred_at FROM status_events").fetchall():
                normalized = normalize_timestamp(row["occurred_at"], "1970-01-01T00:00:00+00:00")
                if normalized != row["occurred_at"]:
                    db.execute(
                        "UPDATE status_events SET occurred_at=? WHERE tenant_id=? AND client_event_id=?",
                        (normalized, row["tenant_id"], row["client_event_id"]),
                    )

    hash_token = staticmethod(token_hash)

    def _new_token(self) -> tuple[str, str]:
        raw = secrets.token_urlsafe(32)
        return raw, token_hash(raw)

    @staticmethod
    def _new_access_code() -> tuple[str, str]:
        # About 100 bits of entropy while remaining easy to read and type.
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        raw = "".join(secrets.choice(alphabet) for _ in range(20))
        grouped = "-".join(raw[index:index + 4] for index in range(0, len(raw), 4))
        return grouped, token_hash(grouped)

    @staticmethod
    def _normalize_access_code(code: str) -> str:
        compact = "".join(character for character in str(code or "").upper() if character.isalnum())
        return "-".join(compact[index:index + 4] for index in range(0, len(compact), 4))

    @staticmethod
    def _audit(
        db: sqlite3.Connection,
        tenant_id: str,
        action: str,
        target_kind: str,
        target_id: str = "",
        details: dict[str, Any] | None = None,
        *,
        actor_kind: str = "bootstrap",
        actor_id: str = "bootstrap",
    ) -> None:
        """Append a security event without ever accepting credential material."""
        db.execute(
            """INSERT INTO security_audit(
                tenant_id,occurred_at,actor_kind,actor_id,action,target_kind,target_id,details_json
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                tenant_id,
                now(),
                actor_kind[:40],
                actor_id[:128],
                action[:80],
                target_kind[:40],
                target_id[:128],
                json.dumps(details or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            ),
        )

    @staticmethod
    def _require_tenant(db: sqlite3.Connection, tenant_id: str) -> None:
        if not db.execute("SELECT 1 FROM tenants WHERE id=?", (tenant_id,)).fetchone():
            raise KeyError("tenant not found")

    def bootstrap(self, name: str, collector_name: str) -> dict[str, str]:
        tenant_id = "ten_" + secrets.token_urlsafe(12)
        collector_id = "col_" + secrets.token_urlsafe(12)
        login_code_id = "login_" + secrets.token_urlsafe(12)
        collector_token, collector_hash = self._new_token()
        access_code, access_code_hash = self._new_access_code()
        with self.lock, self.connection() as db:
            created = now()
            db.execute("INSERT INTO tenants(id,name,created_at) VALUES(?,?,?)", (tenant_id, name[:120], created))
            db.execute("INSERT INTO collectors(id,tenant_id,name,token_hash,created_at) VALUES(?,?,?,?,?)", (collector_id, tenant_id, collector_name[:120], collector_hash, created))
            db.execute("INSERT INTO login_codes(id,tenant_id,code_hash,created_at) VALUES(?,?,?,?)", (login_code_id, tenant_id, access_code_hash, created))
            self._audit(
                db,
                tenant_id,
                "tenant.bootstrap",
                "tenant",
                tenant_id,
                {"collector_id": collector_id, "login_code_id": login_code_id},
            )
        return {"tenant_id": tenant_id, "collector_id": collector_id, "collector_token": collector_token, "access_code": access_code}

    def rotate_access_code(self, tenant_id: str) -> dict[str, Any]:
        """Atomically replace every active access code for one tenant."""
        login_code_id = "login_" + secrets.token_urlsafe(12)
        access_code, access_code_hash = self._new_access_code()
        with self.lock, self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_tenant(db, tenant_id)
            rotated_at = now()
            revoked = db.execute(
                "UPDATE login_codes SET revoked_at=? WHERE tenant_id=? AND revoked_at IS NULL",
                (rotated_at, tenant_id),
            ).rowcount
            db.execute(
                "INSERT INTO login_codes(id,tenant_id,code_hash,created_at) VALUES(?,?,?,?)",
                (login_code_id, tenant_id, access_code_hash, rotated_at),
            )
            self._audit(
                db,
                tenant_id,
                "access_code.rotate",
                "login_code",
                login_code_id,
                {"revoked_count": int(revoked)},
            )
        return {
            "tenant_id": tenant_id,
            "login_code_id": login_code_id,
            "access_code": access_code,
            "revoked_count": int(revoked),
        }

    def revoke_access_codes(self, tenant_id: str) -> int:
        """Atomically revoke all active access codes without touching viewer sessions."""
        with self.lock, self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_tenant(db, tenant_id)
            revoked = db.execute(
                "UPDATE login_codes SET revoked_at=? WHERE tenant_id=? AND revoked_at IS NULL",
                (now(), tenant_id),
            ).rowcount
            self._audit(
                db,
                tenant_id,
                "access_code.revoke",
                "tenant",
                tenant_id,
                {"revoked_count": int(revoked)},
            )
        return int(revoked)

    def rotate_collector_token(self, tenant_id: str, collector_id: str) -> dict[str, str]:
        """Atomically replace one active collector token while preserving its identity."""
        collector_token, collector_hash = self._new_token()
        with self.lock, self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_tenant(db, tenant_id)
            cursor = db.execute(
                """UPDATE collectors SET token_hash=?
                WHERE tenant_id=? AND id=? AND revoked_at IS NULL""",
                (collector_hash, tenant_id, collector_id),
            )
            if cursor.rowcount != 1:
                raise KeyError("collector not found")
            self._audit(
                db,
                tenant_id,
                "collector_token.rotate",
                "collector",
                collector_id,
            )
        return {
            "tenant_id": tenant_id,
            "collector_id": collector_id,
            "collector_token": collector_token,
        }

    def revoke_collector(self, tenant_id: str, collector_id: str) -> bool:
        with self.lock, self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_tenant(db, tenant_id)
            collector = db.execute(
                "SELECT revoked_at FROM collectors WHERE tenant_id=? AND id=?",
                (tenant_id, collector_id),
            ).fetchone()
            if not collector:
                raise KeyError("collector not found")
            if collector["revoked_at"] is not None:
                return False
            cursor = db.execute(
                """UPDATE collectors SET revoked_at=?
                WHERE tenant_id=? AND id=? AND revoked_at IS NULL""",
                (now(), tenant_id, collector_id),
            )
            if cursor.rowcount != 1:  # Defensive: BEGIN IMMEDIATE should make this unreachable.
                raise RuntimeError("collector revocation lost its write lock")
            self._audit(
                db,
                tenant_id,
                "collector_token.revoke",
                "collector",
                collector_id,
            )
        return True

    def revoke_all_viewer_sessions(self, tenant_id: str) -> int:
        with self.lock, self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_tenant(db, tenant_id)
            revoked = db.execute(
                "UPDATE viewer_tokens SET revoked_at=? WHERE tenant_id=? AND revoked_at IS NULL",
                (now(), tenant_id),
            ).rowcount
            self._audit(
                db,
                tenant_id,
                "viewer_session.revoke_all",
                "tenant",
                tenant_id,
                {"revoked_count": int(revoked)},
            )
        return int(revoked)

    def security_audit(self, tenant_id: str, *, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        limit, offset = self._page_bounds(limit, offset)
        with self.lock, self.connection() as db:
            self._require_tenant(db, tenant_id)
            total = int(
                db.execute("SELECT COUNT(*) FROM security_audit WHERE tenant_id=?", (tenant_id,)).fetchone()[0]
            )
            rows = db.execute(
                """SELECT id,tenant_id,occurred_at,actor_kind,actor_id,action,target_kind,target_id,details_json
                FROM security_audit WHERE tenant_id=? ORDER BY occurred_at DESC,id DESC LIMIT ? OFFSET ?""",
                (tenant_id, limit, offset),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.pop("details_json"))
            except (TypeError, json.JSONDecodeError):
                item["details"] = {}
                item.pop("details_json", None)
            items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def exchange_access_code(self, code: str, days: int = 30) -> dict[str, str] | None:
        normalized = self._normalize_access_code(code)
        if not normalized:
            return None
        with self.lock, self.connection() as db:
            # Serialize the read-and-issue sequence with access-code rotation and
            # revoke-all operations, including when multiple processes share SQLite.
            db.execute("BEGIN IMMEDIATE")
            login = db.execute(
                "SELECT tenant_id FROM login_codes WHERE code_hash=? AND revoked_at IS NULL",
                (token_hash(normalized),),
            ).fetchone()
            if not login:
                return None
            viewer_id = "view_" + secrets.token_urlsafe(12)
            session_token, session_hash = self._new_token()
            created_at = now()
            expires_at = (datetime.now(timezone.utc) + timedelta(days=max(1, min(int(days), 365)))).isoformat(timespec="seconds")
            db.execute(
                "INSERT INTO viewer_tokens(id,tenant_id,token_hash,created_at,expires_at) VALUES(?,?,?,?,?)",
                (viewer_id, login["tenant_id"], session_hash, created_at, expires_at),
            )
            return {"session_token": session_token, "tenant_id": str(login["tenant_id"]), "expires_at": expires_at}

    def viewer_identity(self, raw_token: str) -> dict[str, str] | None:
        with self.lock, self.connection() as db:
            row = db.execute(
                """SELECT t.id AS tenant_id, t.name FROM viewer_tokens v
                JOIN tenants t ON t.id=v.tenant_id
                WHERE v.token_hash=? AND v.revoked_at IS NULL AND (v.expires_at IS NULL OR v.expires_at > ?)""",
                (token_hash(raw_token), now()),
            ).fetchone()
            return {"tenant_id": str(row["tenant_id"]), "name": str(row["name"])} if row else None

    def revoke_viewer(self, raw_token: str) -> bool:
        with self.lock, self.connection() as db:
            cursor = db.execute(
                "UPDATE viewer_tokens SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                (now(), token_hash(raw_token)),
            )
            return cursor.rowcount > 0

    def cleanup_expired_sessions(self) -> int:
        """Delete expired browser sessions without changing active or revoked sessions."""
        with self.lock, self.connection() as db:
            cursor = db.execute(
                "DELETE FROM viewer_tokens WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (now(),),
            )
            return int(cursor.rowcount)

    def auth(self, raw_token: str, kind: str) -> dict[str, Any] | None:
        table = "collectors" if kind == "collector" else "viewer_tokens"
        with self.lock, self.connection() as db:
            expiry = " AND (expires_at IS NULL OR expires_at > ?)" if kind != "collector" else ""
            params = (token_hash(raw_token), now()) if kind != "collector" else (token_hash(raw_token),)
            row = db.execute(f"SELECT * FROM {table} WHERE token_hash=? AND revoked_at IS NULL{expiry}", params).fetchone()
            return dict(row) if row else None

    @staticmethod
    def _text(value: Any, maximum: int) -> str:
        return str(value or "")[:maximum]

    @staticmethod
    def _friend_values(friend: dict[str, Any], fallback_updated_at: str) -> tuple[Any, ...]:
        links = friend.get("bioLinks") if isinstance(friend.get("bioLinks"), list) else friend.get("bio_links")
        bounded_links = [str(link)[:2048] for link in links[:32]] if isinstance(links, list) else []
        return (
            Store._text(friend.get("id"), 128), Store._text(friend.get("username"), 128), Store._text(friend.get("displayName") or friend.get("display_name"), 256),
            int(bool(friend.get("isSelf") or friend.get("is_self"))), Store._text(friend.get("status") or "offline", 40), Store._text(friend.get("statusDescription") or friend.get("status_description"), 512),
            Store._text(friend.get("location"), 1024), Store._text(friend.get("platform"), 80), Store._text(friend.get("avatarUrl") or friend.get("avatar_url"), 2048),
            Store._text(friend.get("avatarImageUrl") or friend.get("avatar_image_url"), 2048), Store._text(friend.get("bio"), 8192), json.dumps(bounded_links, ensure_ascii=False),
            Store._text(friend.get("lastSeen") or friend.get("last_seen"), 64) or None, Store._text(friend.get("lastChanged") or friend.get("last_changed"), 64) or None, normalize_timestamp(Store._text(friend.get("updatedAt") or friend.get("updated_at"), 64), fallback_updated_at),
        )

    def ingest(self, tenant_id: str, collector_id: str, friends: list[dict[str, Any]], events: list[dict[str, Any]], source: str = "server-api") -> dict[str, int]:
        changed = 0
        accepted = 0
        accepted_friends = 0
        fallback_updated_at = "1970-01-01T00:00:00+00:00" if source == "import" else now()
        with self.lock, self.connection() as db:
            friend_count = int(db.execute("SELECT COUNT(*) FROM friends WHERE tenant_id=?", (tenant_id,)).fetchone()[0])
            event_count = int(db.execute("SELECT COUNT(*) FROM status_events WHERE tenant_id=?", (tenant_id,)).fetchone()[0])
            for friend in friends[:5000]:
                values = self._friend_values(friend, fallback_updated_at)
                if not values[0]:
                    continue
                old = db.execute("SELECT status, location, platform FROM friends WHERE tenant_id=? AND id=?", (tenant_id, values[0])).fetchone()
                if old is None and friend_count >= self.friend_limit:
                    raise ValueError(f"租户玩家数量已达到上限（{self.friend_limit}）")
                cursor = db.execute(
                    """INSERT INTO friends(tenant_id,id,username,display_name,is_self,status,status_description,location,platform,avatar_url,avatar_image_url,bio,bio_links,last_seen,last_changed,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(tenant_id,id) DO UPDATE SET username=excluded.username,display_name=excluded.display_name,is_self=excluded.is_self,status=excluded.status,status_description=excluded.status_description,location=excluded.location,platform=excluded.platform,avatar_url=excluded.avatar_url,avatar_image_url=excluded.avatar_image_url,bio=excluded.bio,bio_links=excluded.bio_links,last_seen=excluded.last_seen,last_changed=excluded.last_changed,updated_at=excluded.updated_at
                    WHERE excluded.updated_at >= friends.updated_at""",
                    (tenant_id, *values),
                )
                accepted_friends += int(cursor.rowcount > 0)
                if old is None and cursor.rowcount > 0:
                    friend_count += 1
                if cursor.rowcount > 0 and old and (old["status"], old["location"], old["platform"]) != (values[4], values[6], values[7]):
                    changed += 1
            for event in events[:10000]:
                event_id = self._text(event.get("client_event_id") or event.get("id"), 256)
                if not event_id:
                    event_id = self._text(
                        f"{event.get('friend_id') or event.get('friendId') or ''}:{event.get('occurred_at') or ''}:{event.get('new_status') or ''}",
                        256,
                    )
                friend_id = self._text(event.get("friend_id") or event.get("friendId"), 128)
                if not event_id or not friend_id or not event.get("occurred_at"):
                    continue
                # Namespace client ids by the authenticated collector so two devices
                # in one tenant cannot accidentally deduplicate one another.
                namespaced_event_id = f"{collector_id}:{event_id}"
                if event_count >= self.event_limit:
                    duplicate = db.execute(
                        "SELECT 1 FROM status_events WHERE tenant_id=? AND client_event_id=?",
                        (tenant_id, namespaced_event_id),
                    ).fetchone()
                    if duplicate:
                        continue
                    raise ValueError(f"租户历史记录已达到上限（{self.event_limit}）")
                cursor = db.execute(
                    """INSERT OR IGNORE INTO status_events(tenant_id,client_event_id,friend_id,occurred_at,old_status,new_status,location,platform,source)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        tenant_id,
                        namespaced_event_id,
                        friend_id,
                        normalize_timestamp(self._text(event["occurred_at"], 64), fallback_updated_at),
                        self._text(event.get("old_status") or "unknown", 40),
                        self._text(event.get("new_status") or "offline", 40),
                        self._text(event.get("location"), 1024),
                        self._text(event.get("platform"), 80),
                        self._text(event.get("source") or source, 80),
                    ),
                )
                accepted += int(cursor.rowcount > 0)
                event_count += int(cursor.rowcount > 0)
            db.execute("UPDATE collectors SET last_sync=?, last_error='' WHERE id=?", (now(), collector_id))
        return {"friends": accepted_friends, "events": accepted, "changed": changed}

    def mark_collector_error(self, collector_id: str, error: str) -> None:
        with self.lock, self.connection() as db:
            db.execute("UPDATE collectors SET last_error=? WHERE id=?", (error[:500], collector_id))

    @staticmethod
    def _page_bounds(limit: int, offset: int) -> tuple[int, int]:
        return max(1, min(int(limit), 200)), max(0, int(offset))

    @staticmethod
    def _like(value: str) -> str:
        escaped = str(value or "").strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    def ready(self) -> bool:
        try:
            with self.connection() as db:
                db.execute("SELECT 1 FROM tenants LIMIT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    def overview(self, tenant_id: str, stale_after_seconds: int = 300) -> dict[str, Any]:
        seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
        with self.lock, self.connection() as db:
            friend_counts = db.execute(
                """SELECT COUNT(*) AS tracked_count,
                SUM(CASE WHEN status <> 'offline' THEN 1 ELSE 0 END) AS online_count
                FROM friends WHERE tenant_id=?""",
                (tenant_id,),
            ).fetchone()
            event_counts = db.execute(
                """SELECT COUNT(*) AS event_total,
                SUM(CASE WHEN occurred_at >= ? THEN 1 ELSE 0 END) AS change_count_7d
                FROM status_events WHERE tenant_id=?""",
                (seven_days_ago, tenant_id),
            ).fetchone()
            status_counts = {
                str(row["status"]): int(row["count"])
                for row in db.execute(
                    "SELECT status, COUNT(*) AS count FROM friends WHERE tenant_id=? GROUP BY status",
                    (tenant_id,),
                ).fetchall()
            }
            sync = db.execute(
                "SELECT last_sync, last_error FROM collectors WHERE tenant_id=? ORDER BY last_sync DESC LIMIT 1",
                (tenant_id,),
            ).fetchone()
        last_sync = str(sync["last_sync"]) if sync and sync["last_sync"] else None
        collector_error = str(sync["last_error"] or "") if sync else ""
        sync_age_seconds: int | None = None
        if last_sync:
            try:
                synced_at = datetime.fromisoformat(last_sync.replace("Z", "+00:00"))
                if synced_at.tzinfo is None:
                    synced_at = synced_at.replace(tzinfo=timezone.utc)
                sync_age_seconds = max(0, int((datetime.now(timezone.utc) - synced_at).total_seconds()))
            except ValueError:
                collector_error = collector_error or "invalid last sync timestamp"
        if collector_error:
            collector_state = "error"
        elif not last_sync:
            collector_state = "never"
        elif sync_age_seconds is None or sync_age_seconds > max(30, int(stale_after_seconds)):
            collector_state = "stale"
        else:
            collector_state = "fresh"
        return {
            "tracked_count": int(friend_counts["tracked_count"] or 0),
            "online_count": int(friend_counts["online_count"] or 0),
            "event_total": int(event_counts["event_total"] or 0),
            "change_count_7d": int(event_counts["change_count_7d"] or 0),
            "status_counts": status_counts,
            "last_sync": last_sync,
            "collector_error": collector_error,
            "collector_state": collector_state,
            "sync_age_seconds": sync_age_seconds,
            "stale_after_seconds": max(30, int(stale_after_seconds)),
        }

    def friends_page(
        self,
        tenant_id: str,
        *,
        query: str = "",
        status: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        limit, offset = self._page_bounds(limit, offset)
        clauses = ["tenant_id=?"]
        params: list[Any] = [tenant_id]
        if query.strip():
            clauses.append("(display_name LIKE ? ESCAPE '\\' OR username LIKE ? ESCAPE '\\' OR id LIKE ? ESCAPE '\\')")
            needle = self._like(query)
            params.extend([needle, needle, needle])
        normalized_status = status.strip().lower()
        if normalized_status == "online":
            clauses.append("status <> 'offline'")
        elif normalized_status:
            clauses.append("status=?")
            params.append(normalized_status)
        where = " AND ".join(clauses)
        with self.lock, self.connection() as db:
            total = int(db.execute(f"SELECT COUNT(*) FROM friends WHERE {where}", params).fetchone()[0])
            items = [
                dict(row)
                for row in db.execute(
                    f"SELECT * FROM friends WHERE {where} ORDER BY is_self DESC, display_name COLLATE NOCASE LIMIT ? OFFSET ?",
                    [*params, limit, offset],
                ).fetchall()
            ]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def events_page(
        self,
        tenant_id: str,
        *,
        query: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        limit, offset = self._page_bounds(limit, offset)
        clauses = ["e.tenant_id=?"]
        params: list[Any] = [tenant_id]
        if query.strip():
            clauses.append(
                "(f.display_name LIKE ? ESCAPE '\\' OR f.username LIKE ? ESCAPE '\\' "
                "OR e.new_status LIKE ? ESCAPE '\\' OR e.location LIKE ? ESCAPE '\\' OR e.source LIKE ? ESCAPE '\\')"
            )
            needle = self._like(query)
            params.extend([needle] * 5)
        where = " AND ".join(clauses)
        join = "FROM status_events e LEFT JOIN friends f ON f.tenant_id=e.tenant_id AND f.id=e.friend_id"
        with self.lock, self.connection() as db:
            total = int(db.execute(f"SELECT COUNT(*) {join} WHERE {where}", params).fetchone()[0])
            items = [
                dict(row)
                for row in db.execute(
                    f"""SELECT e.*, f.display_name, f.username, f.avatar_image_url
                    {join} WHERE {where}
                    ORDER BY e.occurred_at DESC, e.client_event_id DESC LIMIT ? OFFSET ?""",
                    [*params, limit, offset],
                ).fetchall()
            ]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def data(self, tenant_id: str, limit: int = 1000) -> dict[str, Any]:
        limit = max(0, min(int(limit), 10000))
        with self.lock, self.connection() as db:
            friends = [dict(row) for row in db.execute("SELECT * FROM friends WHERE tenant_id=? ORDER BY is_self DESC, display_name COLLATE NOCASE", (tenant_id,)).fetchall()]
            events = [dict(row) for row in db.execute("SELECT * FROM status_events WHERE tenant_id=? ORDER BY occurred_at DESC LIMIT ?", (tenant_id, limit or 1)).fetchall()] if limit else []
        summary = self.overview(tenant_id)
        return {
            "friends": friends,
            "events": events,
            "last_sync": summary["last_sync"],
            "collector_error": summary["collector_error"],
            "event_total": summary["event_total"],
            "change_count_7d": summary["change_count_7d"],
        }

    def export_json(self, tenant_id: str) -> dict[str, Any]:
        with self.lock, self.connection() as db:
            friends = [dict(row) for row in db.execute("SELECT * FROM friends WHERE tenant_id=? ORDER BY id", (tenant_id,)).fetchall()]
            events = [dict(row) for row in db.execute("SELECT * FROM status_events WHERE tenant_id=? ORDER BY occurred_at, client_event_id", (tenant_id,)).fetchall()]
        return {"format": "vrchat-monitor-hosted-backup", "version": 1, "exported_at": now(), "friends": friends, "status_events": events}

    def import_json(self, tenant_id: str, payload: dict[str, Any]) -> dict[str, int]:
        version = payload.get("version")
        if (
            payload.get("format") not in ("vrchat-monitor-hosted-backup", "vrchat-monitor-backup")
            or version != 1
            or isinstance(version, bool)
        ):
            raise ValueError("不是有效的兼容备份文件")
        friends = payload.get("friends", [])
        events = payload.get("status_events", [])
        if (
            not isinstance(friends, list)
            or not isinstance(events, list)
            or any(not isinstance(friend, dict) for friend in friends)
            or any(not isinstance(event, dict) for event in events)
        ):
            raise ValueError("备份文件包含无效的数据项")
        return self.ingest(tenant_id, "import", friends, events, "import")
