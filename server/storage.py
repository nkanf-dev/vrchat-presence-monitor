from __future__ import annotations

import gzip
import hashlib
import json
import re
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any


BACKUP_FORMAT = "vrchat-monitor-hosted-backup"
BACKUP_VERSION = 2
BACKUP_ENVELOPE_BUDGET = 1024
BACKUP_COMPRESSION_MARGIN = 256
BACKUP_EVENT_ID_LIMIT = 512
DEFAULT_MAX_BACKUP_BYTES = 32 * 1024 * 1024
LEGACY_EVENT_ID_PATTERN = re.compile(r"^legacy_event_([0-9]+)_([0-9a-f]{64})$")
LEGACY_EVENT_ALIAS_PATTERN = re.compile(r"^local-[0-9]+$")
FRIEND_EXPORT_COLUMNS = (
    "id",
    "username",
    "display_name",
    "is_self",
    "status",
    "status_description",
    "location",
    "platform",
    "avatar_url",
    "avatar_image_url",
    "bio",
    "bio_links",
    "last_seen",
    "last_changed",
    "updated_at",
)
EVENT_EXPORT_COLUMNS = (
    "client_event_id",
    "friend_id",
    "occurred_at",
    "old_status",
    "new_status",
    "location",
    "platform",
    "source",
)
EVENT_DB_EXPORT_COLUMNS = (*EVENT_EXPORT_COLUMNS, "previous_event_id")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


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
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


class Store:
    def __init__(
        self,
        path: str,
        *,
        friend_limit: int = 10_000,
        event_limit: int = 1_000_000,
        max_backup_bytes: int = DEFAULT_MAX_BACKUP_BYTES,
    ):
        self.path = path
        self.friend_limit = max(1, int(friend_limit))
        self.event_limit = max(1, int(event_limit))
        self.max_backup_bytes = int(max_backup_bytes)
        if self.max_backup_bytes < BACKUP_ENVELOPE_BUDGET:
            raise ValueError(
                f"max_backup_bytes must be at least {BACKUP_ENVELOPE_BUDGET}"
            )
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
            BEGIN IMMEDIATE;
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
                source TEXT NOT NULL DEFAULT 'server-api', previous_event_id TEXT,
                PRIMARY KEY(tenant_id, client_event_id),
                FOREIGN KEY(tenant_id, friend_id) REFERENCES friends(tenant_id, id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_hosted_events_tenant_time ON status_events(tenant_id, occurred_at);
            CREATE TABLE IF NOT EXISTS raw_fetches (
                id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, occurred_at TEXT NOT NULL, method TEXT NOT NULL,
                path TEXT NOT NULL, status_code INTEGER, content_type TEXT NOT NULL DEFAULT '', body BLOB NOT NULL DEFAULT X'', error TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS portable_backup_usage (
                tenant_id TEXT PRIMARY KEY,
                friend_count INTEGER NOT NULL DEFAULT 0,
                friend_bytes INTEGER NOT NULL DEFAULT 0,
                event_count INTEGER NOT NULL DEFAULT 0,
                event_bytes INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """)
            viewer_columns = {row["name"] for row in db.execute("PRAGMA table_info(viewer_tokens)").fetchall()}
            if "expires_at" not in viewer_columns:
                db.execute("ALTER TABLE viewer_tokens ADD COLUMN expires_at TEXT")
            event_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(status_events)").fetchall()
            }
            if "previous_event_id" not in event_columns:
                db.execute("ALTER TABLE status_events ADD COLUMN previous_event_id TEXT")
            db.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_hosted_events_previous_id
                ON status_events(tenant_id,previous_event_id)
                WHERE previous_event_id IS NOT NULL"""
            )
            for row in db.execute(
                "SELECT tenant_id,id,updated_at,bio_links FROM friends"
            ).fetchall():
                normalized = normalize_timestamp(row["updated_at"], "1970-01-01T00:00:00+00:00")
                if normalized != row["updated_at"]:
                    db.execute("UPDATE friends SET updated_at=? WHERE tenant_id=? AND id=?", (normalized, row["tenant_id"], row["id"]))
                try:
                    links = json.loads(row["bio_links"])
                except (TypeError, json.JSONDecodeError):
                    links = None
                if isinstance(links, list):
                    canonical_links = json.dumps(
                        [str(link) for link in links],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    if canonical_links != row["bio_links"]:
                        db.execute(
                            "UPDATE friends SET bio_links=? WHERE tenant_id=? AND id=?",
                            (canonical_links, row["tenant_id"], row["id"]),
                        )
            for row in db.execute("SELECT tenant_id, client_event_id, occurred_at FROM status_events").fetchall():
                normalized = normalize_timestamp(row["occurred_at"], "1970-01-01T00:00:00+00:00")
                if normalized != row["occurred_at"]:
                    db.execute(
                        "UPDATE status_events SET occurred_at=? WHERE tenant_id=? AND client_event_id=?",
                        (normalized, row["tenant_id"], row["client_event_id"]),
                    )
            if db.execute(
                "SELECT 1 FROM schema_meta WHERE key='canonical_event_ids_v2'"
            ).fetchone() is None:
                self._migrate_collector_prefixed_event_ids(db)
                db.execute(
                    "INSERT INTO schema_meta(key,value) VALUES('canonical_event_ids_v2',?)",
                    (now(),),
                )
            self._rebuild_backup_usage(db)

    @staticmethod
    def _migrate_collector_prefixed_event_ids(db: sqlite3.Connection) -> None:
        """Canonicalize IDs written by the pre-v2 hosted collector namespace.

        The old server persisted ``<collector-id>:<client-event-id>``.  This is
        deliberately a one-shot migration keyed by exact collector rows; new
        canonical IDs are never guessed from a ``col_*`` prefix.
        """
        collectors = db.execute(
            "SELECT id,tenant_id FROM collectors ORDER BY tenant_id,id"
        ).fetchall()
        semantic_columns = EVENT_EXPORT_COLUMNS[1:]
        for collector in collectors:
            prefix = f"{collector['id']}:"
            rows = db.execute(
                f"""SELECT {','.join(EVENT_EXPORT_COLUMNS)} FROM status_events
                WHERE tenant_id=? AND substr(client_event_id,1,?)=?
                ORDER BY client_event_id""",
                (collector["tenant_id"], len(prefix), prefix),
            ).fetchall()
            for row in rows:
                canonical_id = str(row["client_event_id"])[len(prefix):]
                if not canonical_id:
                    raise RuntimeError("旧版历史记录包含空的稳定 ID")
                existing = db.execute(
                    f"""SELECT {','.join(semantic_columns)} FROM status_events
                    WHERE tenant_id=? AND client_event_id=?""",
                    (collector["tenant_id"], canonical_id),
                ).fetchone()
                incoming = tuple(row[column] for column in semantic_columns)
                if existing is None:
                    db.execute(
                        """UPDATE status_events SET client_event_id=?
                        WHERE tenant_id=? AND client_event_id=?""",
                        (canonical_id, collector["tenant_id"], row["client_event_id"]),
                    )
                elif tuple(existing) == incoming:
                    db.execute(
                        "DELETE FROM status_events WHERE tenant_id=? AND client_event_id=?",
                        (collector["tenant_id"], row["client_event_id"]),
                    )
                else:
                    raise RuntimeError(
                        "旧版历史记录命名空间迁移遇到内容冲突；数据库未被静默改写"
                    )

    @staticmethod
    def _export_item(row: Any, columns: tuple[str, ...]) -> dict[str, Any]:
        return {column: row[column] for column in columns}

    @classmethod
    def _export_event_item(cls, row: Any) -> dict[str, Any]:
        item = cls._export_item(row, EVENT_EXPORT_COLUMNS)
        previous_event_id = row["previous_event_id"]
        if previous_event_id:
            item["previous_event_ids"] = [previous_event_id]
        return item

    @staticmethod
    def _encoded_item_size(item: dict[str, Any]) -> int:
        return len(
            json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )

    @classmethod
    def _portable_backup_size(
        cls,
        friend_count: int,
        friend_bytes: int,
        event_count: int,
        event_bytes: int,
    ) -> int:
        return (
            BACKUP_ENVELOPE_BUDGET
            + max(0, int(friend_bytes))
            + max(0, int(event_bytes))
            + max(0, int(friend_count) - 1)
            + max(0, int(event_count) - 1)
        )

    def _assert_backup_fits(
        self,
        friend_count: int,
        friend_bytes: int,
        event_count: int,
        event_bytes: int,
    ) -> None:
        size = self._portable_backup_size(
            friend_count,
            friend_bytes,
            event_count,
            event_bytes,
        )
        if size > self.max_backup_bytes:
            raise ValueError(
                "租户数据已达到可恢复备份容量上限；请先导出备份或由部署者提高 MAX_IMPORT_BYTES"
            )

    def _rebuild_backup_usage(self, db: sqlite3.Connection) -> None:
        db.execute("DELETE FROM portable_backup_usage")
        for tenant in db.execute("SELECT id FROM tenants ORDER BY id").fetchall():
            tenant_id = str(tenant["id"])
            friend_count = 0
            friend_bytes = 0
            for row in db.execute(
                f"SELECT {','.join(FRIEND_EXPORT_COLUMNS)} FROM friends WHERE tenant_id=?",
                (tenant_id,),
            ):
                friend_count += 1
                friend_bytes += self._encoded_item_size(
                    self._export_item(row, FRIEND_EXPORT_COLUMNS)
                )
            event_count = 0
            event_bytes = 0
            for row in db.execute(
                f"SELECT {','.join(EVENT_DB_EXPORT_COLUMNS)} FROM status_events WHERE tenant_id=?",
                (tenant_id,),
            ):
                event_count += 1
                event_bytes += self._encoded_item_size(self._export_event_item(row))
            db.execute(
                """INSERT INTO portable_backup_usage(
                    tenant_id,friend_count,friend_bytes,event_count,event_bytes
                ) VALUES(?,?,?,?,?)""",
                (tenant_id, friend_count, friend_bytes, event_count, event_bytes),
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
            db.execute(
                "INSERT INTO portable_backup_usage(tenant_id) VALUES(?)",
                (tenant_id,),
            )
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
    def _text(value: Any, maximum: int, label: str = "字段") -> str:
        text = str(value or "")
        if len(text) > maximum:
            raise ValueError(f"{label}超过长度上限（{maximum}）")
        return text

    @staticmethod
    def _boolean(value: Any, label: str) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int) and value in (0, 1):
            return value
        raise ValueError(f"{label}格式无效")

    @classmethod
    def _timestamp(cls, value: Any, fallback: str, label: str) -> str:
        text = cls._text(value, 64, label).strip()
        if not text:
            return fallback
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"{label}不是有效时间") from error
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")

    @staticmethod
    def _friend_values(friend: dict[str, Any], fallback_updated_at: str) -> tuple[Any, ...]:
        links = friend.get("bioLinks") if "bioLinks" in friend else friend.get("bio_links")
        if isinstance(links, str):
            try:
                decoded_links = json.loads(links)
            except json.JSONDecodeError as error:
                raise ValueError("玩家简介链接格式无效") from error
            if not isinstance(decoded_links, list):
                raise ValueError("玩家简介链接格式无效")
            links = decoded_links
        if links is None:
            links = []
        if not isinstance(links, list):
            raise ValueError("玩家简介链接格式无效")
        if len(links) > 32:
            raise ValueError("玩家简介链接超过数量上限（32）")
        bounded_links = [Store._text(link, 2048, "玩家简介链接") for link in links]
        is_self = friend["isSelf"] if "isSelf" in friend else friend.get("is_self", False)
        return (
            Store._text(friend.get("id"), 128, "玩家 ID"),
            Store._text(friend.get("username"), 128, "用户名"),
            Store._text(friend.get("displayName") or friend.get("display_name"), 256, "显示名称"),
            Store._boolean(is_self, "本人标记"),
            Store._text(friend.get("status") or "offline", 40, "状态"),
            Store._text(friend.get("statusDescription") or friend.get("status_description"), 512, "状态简介"),
            Store._text(friend.get("location"), 1024, "位置"),
            Store._text(friend.get("platform"), 80, "平台"),
            Store._text(friend.get("avatarUrl") or friend.get("avatar_url"), 2048, "头像 URL"),
            Store._text(friend.get("avatarImageUrl") or friend.get("avatar_image_url"), 2048, "头像图片 URL"),
            Store._text(friend.get("bio"), 8192, "简介"),
            json.dumps(bounded_links, ensure_ascii=False, separators=(",", ":")),
            Store._text(friend.get("lastSeen") or friend.get("last_seen"), 64, "最后在线时间") or None,
            Store._text(friend.get("lastChanged") or friend.get("last_changed"), 64, "最后变化时间") or None,
            Store._timestamp(friend.get("updatedAt") or friend.get("updated_at"), fallback_updated_at, "玩家更新时间"),
        )

    def ingest(
        self,
        tenant_id: str,
        collector_id: str,
        friends: list[dict[str, Any]],
        events: list[dict[str, Any]],
        source: str = "server-api",
        *,
        friend_batch_limit: int | None = 5000,
        event_batch_limit: int | None = 10000,
    ) -> dict[str, int]:
        if friend_batch_limit is not None and len(friends) > friend_batch_limit:
            raise ValueError(f"单批玩家记录超过上限（{friend_batch_limit}）")
        if event_batch_limit is not None and len(events) > event_batch_limit:
            raise ValueError(f"单批历史记录超过上限（{event_batch_limit}）")
        changed = 0
        accepted = 0
        accepted_friends = 0
        fallback_updated_at = "1970-01-01T00:00:00+00:00" if source == "import" else now()
        with self.lock, self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            usage = db.execute(
                """SELECT friend_count,friend_bytes,event_count,event_bytes
                FROM portable_backup_usage WHERE tenant_id=?""",
                (tenant_id,),
            ).fetchone()
            if usage is None:
                raise KeyError("tenant not found")
            friend_count = int(usage["friend_count"])
            friend_bytes = int(usage["friend_bytes"])
            event_count = int(usage["event_count"])
            event_bytes = int(usage["event_bytes"])
            initial_backup_size = self._portable_backup_size(
                friend_count,
                friend_bytes,
                event_count,
                event_bytes,
            )
            for friend in friends:
                values = self._friend_values(friend, fallback_updated_at)
                if not values[0]:
                    raise ValueError("玩家记录缺少 ID")
                old = db.execute(
                    f"SELECT {','.join(FRIEND_EXPORT_COLUMNS)} FROM friends WHERE tenant_id=? AND id=?",
                    (tenant_id, values[0]),
                ).fetchone()
                if source == "import" and old is not None:
                    if values[14] < old["updated_at"]:
                        continue
                    if values[14] == old["updated_at"]:
                        if tuple(old[column] for column in FRIEND_EXPORT_COLUMNS) != values:
                            raise ValueError(
                                "玩家快照时间相同但内容不同；导入已回滚，未静默选择任一版本"
                            )
                        continue
                if old is None and friend_count >= self.friend_limit:
                    raise ValueError(f"租户玩家数量已达到上限（{self.friend_limit}）")
                cursor = db.execute(
                    """INSERT INTO friends(tenant_id,id,username,display_name,is_self,status,status_description,location,platform,avatar_url,avatar_image_url,bio,bio_links,last_seen,last_changed,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(tenant_id,id) DO UPDATE SET username=excluded.username,display_name=excluded.display_name,is_self=excluded.is_self,status=excluded.status,status_description=excluded.status_description,location=excluded.location,platform=excluded.platform,avatar_url=excluded.avatar_url,avatar_image_url=excluded.avatar_image_url,bio=excluded.bio,bio_links=excluded.bio_links,last_seen=excluded.last_seen,last_changed=excluded.last_changed,updated_at=excluded.updated_at
                    WHERE excluded.updated_at >= friends.updated_at""",
                    (tenant_id, *values),
                )
                accepted_friends += int(cursor.rowcount > 0)
                if cursor.rowcount > 0:
                    item = dict(zip(FRIEND_EXPORT_COLUMNS, values))
                    friend_bytes += self._encoded_item_size(item)
                    if old is None:
                        friend_count += 1
                    else:
                        friend_bytes -= self._encoded_item_size(
                            self._export_item(old, FRIEND_EXPORT_COLUMNS)
                        )
                        if (old["status"], old["location"], old["platform"]) != (
                            values[4],
                            values[6],
                            values[7],
                        ):
                            changed += 1
            for event in events:
                supplied_event_id = event.get("client_event_id") or event.get("id")
                if supplied_event_id:
                    event_id = self._text(
                        supplied_event_id,
                        BACKUP_EVENT_ID_LIMIT if source == "import" else 256,
                        "历史记录稳定 ID",
                    )
                else:
                    identity = json.dumps(
                        [
                            event.get("friend_id") or event.get("friendId") or "",
                            event.get("occurred_at") or "",
                            event.get("old_status") or "unknown",
                            event.get("new_status") or "offline",
                            event.get("location") or "",
                            event.get("platform") or "",
                            event.get("source") or source,
                        ],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    event_id = f"legacy_event_{hashlib.sha256(identity).hexdigest()}"
                if event_id != event_id.strip():
                    raise ValueError("历史记录稳定 ID 不能包含首尾空白")
                previous_event_ids = event.get("previous_event_ids", [])
                if previous_event_ids is None:
                    previous_event_ids = []
                if not isinstance(previous_event_ids, list) or len(previous_event_ids) > 1:
                    raise ValueError("历史记录旧稳定 ID 格式无效")
                migrated_match = LEGACY_EVENT_ID_PATTERN.fullmatch(event_id)
                expected_alias = (
                    f"local-{migrated_match.group(1)}" if migrated_match else None
                )
                aliases: list[str] = []
                for previous_event_id in previous_event_ids:
                    alias = self._text(previous_event_id, 64, "历史记录旧稳定 ID")
                    if (
                        not alias
                        or alias != alias.strip()
                        or LEGACY_EVENT_ALIAS_PATTERN.fullmatch(alias) is None
                        or expected_alias is None
                        or alias != expected_alias
                        or alias == event_id
                    ):
                        raise ValueError("历史记录旧稳定 ID 格式无效")
                    aliases.append(alias)
                friend_id = self._text(
                    event.get("friend_id") or event.get("friendId"),
                    128,
                    "历史记录玩家 ID",
                )
                if not event_id or not friend_id or not event.get("occurred_at"):
                    raise ValueError("历史记录缺少稳定 ID、玩家或时间")
                if db.execute(
                    "SELECT 1 FROM friends WHERE tenant_id=? AND id=?",
                    (tenant_id, friend_id),
                ).fetchone() is None:
                    raise ValueError("历史记录引用了不存在的玩家")
                event_values = (
                    friend_id,
                    self._timestamp(event["occurred_at"], fallback_updated_at, "历史记录时间"),
                    self._text(event.get("old_status") or "unknown", 40, "旧状态"),
                    self._text(event.get("new_status") or "offline", 40, "新状态"),
                    self._text(event.get("location"), 1024, "历史位置"),
                    self._text(event.get("platform"), 80, "历史平台"),
                    self._text(event.get("source") or source, 80, "历史来源"),
                )
                existing_by_id: dict[str, sqlite3.Row] = {}
                for candidate_id in dict.fromkeys((event_id, *aliases)):
                    for existing in db.execute(
                        f"""SELECT {','.join(EVENT_DB_EXPORT_COLUMNS)} FROM status_events
                        WHERE tenant_id=? AND (
                            client_event_id=? OR previous_event_id=?
                        )""",
                        (tenant_id, candidate_id, candidate_id),
                    ).fetchall():
                        existing_by_id[str(existing["client_event_id"])] = existing
                existing_rows = list(existing_by_id.values())
                if existing_rows:
                    if any(
                        tuple(existing[column] for column in EVENT_EXPORT_COLUMNS[1:])
                        != event_values
                        for existing in existing_rows
                    ):
                        raise ValueError("历史记录稳定 ID 与现有内容冲突")
                    if len(existing_rows) > 1:
                        raise ValueError("历史记录新旧稳定 ID 指向了多条现有记录")
                    if aliases:
                        existing = existing_rows[0]
                        alias = aliases[0]
                        current_alias = existing["previous_event_id"]
                        if existing["client_event_id"] != alias:
                            if current_alias and current_alias != alias:
                                raise ValueError("历史记录旧稳定 ID 与现有映射冲突")
                            if not current_alias:
                                old_item = self._export_event_item(existing)
                                db.execute(
                                    """UPDATE status_events SET previous_event_id=?
                                    WHERE tenant_id=? AND client_event_id=?""",
                                    (alias, tenant_id, existing["client_event_id"]),
                                )
                                new_item = dict(old_item)
                                new_item["previous_event_ids"] = [alias]
                                event_bytes += (
                                    self._encoded_item_size(new_item)
                                    - self._encoded_item_size(old_item)
                                )
                    continue
                if event_count >= self.event_limit:
                    raise ValueError(f"租户历史记录已达到上限（{self.event_limit}）")
                db.execute(
                    """INSERT INTO status_events(
                        tenant_id,client_event_id,friend_id,occurred_at,old_status,
                        new_status,location,platform,source,previous_event_id
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        tenant_id,
                        event_id,
                        *event_values,
                        aliases[0] if aliases else None,
                    ),
                )
                accepted += 1
                event_count += 1
                event_item = dict(zip(EVENT_EXPORT_COLUMNS, (event_id, *event_values)))
                if aliases:
                    event_item["previous_event_ids"] = [aliases[0]]
                event_bytes += self._encoded_item_size(event_item)
            final_backup_size = self._portable_backup_size(
                friend_count,
                friend_bytes,
                event_count,
                event_bytes,
            )
            if final_backup_size > self.max_backup_bytes:
                if final_backup_size <= initial_backup_size:
                    pass
                elif source == "import" and self._backup_representation_fits(db, tenant_id):
                    pass
                else:
                    self._assert_backup_fits(
                        friend_count,
                        friend_bytes,
                        event_count,
                        event_bytes,
                    )
            db.execute(
                """UPDATE portable_backup_usage SET
                    friend_count=?,friend_bytes=?,event_count=?,event_bytes=?
                    WHERE tenant_id=?""",
                (friend_count, friend_bytes, event_count, event_bytes, tenant_id),
            )
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
                    f"""SELECT {','.join(FRIEND_EXPORT_COLUMNS)} FROM friends
                    WHERE {where} ORDER BY is_self DESC, display_name COLLATE NOCASE
                    LIMIT ? OFFSET ?""",
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
                    f"""SELECT {','.join(f'e.{column}' for column in EVENT_EXPORT_COLUMNS)},
                    f.display_name, f.username, f.avatar_image_url
                    {join} WHERE {where}
                    ORDER BY e.occurred_at DESC, e.client_event_id DESC LIMIT ? OFFSET ?""",
                    [*params, limit, offset],
                ).fetchall()
            ]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def data(self, tenant_id: str, limit: int = 1000) -> dict[str, Any]:
        limit = max(0, min(int(limit), 10000))
        with self.lock, self.connection() as db:
            friends = [
                dict(row)
                for row in db.execute(
                    f"""SELECT {','.join(FRIEND_EXPORT_COLUMNS)} FROM friends
                    WHERE tenant_id=? ORDER BY is_self DESC, display_name COLLATE NOCASE""",
                    (tenant_id,),
                ).fetchall()
            ]
            events = [
                dict(row)
                for row in db.execute(
                    f"""SELECT {','.join(EVENT_EXPORT_COLUMNS)} FROM status_events
                    WHERE tenant_id=? ORDER BY occurred_at DESC LIMIT ?""",
                    (tenant_id, limit or 1),
                ).fetchall()
            ] if limit else []
        summary = self.overview(tenant_id)
        return {
            "friends": friends,
            "events": events,
            "last_sync": summary["last_sync"],
            "collector_error": summary["collector_error"],
            "event_total": summary["event_total"],
            "change_count_7d": summary["change_count_7d"],
        }

    @classmethod
    def _backup_payload(
        cls,
        db: sqlite3.Connection,
        tenant_id: str,
        exported_at: str | None = None,
    ) -> dict[str, Any]:
        friends = [
            cls._export_item(row, FRIEND_EXPORT_COLUMNS)
            for row in db.execute(
                f"SELECT {','.join(FRIEND_EXPORT_COLUMNS)} FROM friends WHERE tenant_id=? ORDER BY id",
                (tenant_id,),
            ).fetchall()
        ]
        events = [
            cls._export_event_item(row)
            for row in db.execute(
                f"""SELECT {','.join(EVENT_DB_EXPORT_COLUMNS)} FROM status_events
                WHERE tenant_id=? ORDER BY occurred_at, client_event_id""",
                (tenant_id,),
            ).fetchall()
        ]
        return {
            "format": BACKUP_FORMAT,
            "version": BACKUP_VERSION,
            "exported_at": exported_at or now(),
            "friends": friends,
            "status_events": events,
        }

    def _backup_representation_fits(
        self,
        db: sqlite3.Connection,
        tenant_id: str,
    ) -> bool:
        payload = self._backup_payload(db, tenant_id)
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) <= self.max_backup_bytes:
            return True
        safe_limit = max(0, self.max_backup_bytes - BACKUP_COMPRESSION_MARGIN)
        return len(gzip.compress(encoded, compresslevel=9, mtime=0)) <= safe_limit

    def export_json(self, tenant_id: str) -> dict[str, Any]:
        with self.lock, self.connection() as db:
            db.execute("BEGIN")
            return self._backup_payload(db, tenant_id)

    def import_json(self, tenant_id: str, payload: dict[str, Any]) -> dict[str, int]:
        backup_format = payload.get("format")
        version = payload.get("version")
        if (
            not (
                (backup_format == "vrchat-monitor-hosted-backup" and version in (1, 2))
                or (backup_format == "vrchat-monitor-backup" and version in (1, 2))
            )
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
        if any(not friend.get("id") for friend in friends):
            raise ValueError("备份文件包含缺少 ID 的玩家")
        normalized_events = []
        for index, event in enumerate(events):
            friend_id = event.get("friend_id") or event.get("friendId")
            occurred_at = event.get("occurred_at")
            if not friend_id or not occurred_at:
                raise ValueError("备份文件包含缺少玩家或时间的历史记录")
            event_id = event.get("client_event_id")
            if version == 2:
                if (
                    not isinstance(event_id, str)
                    or not event_id
                    or event_id != event_id.strip()
                    or len(event_id) > BACKUP_EVENT_ID_LIMIT
                ):
                    raise ValueError("v2 备份包含缺少稳定 ID 的历史记录")
            elif not isinstance(event_id, str) or not event_id:
                legacy_id = event.get("id", f"row-{index}")
                identity = json.dumps(
                    [
                        legacy_id,
                        str(friend_id),
                        str(occurred_at),
                        str(event.get("old_status") or "unknown"),
                        str(event.get("new_status") or "offline"),
                        str(event.get("location") or ""),
                        str(event.get("platform") or ""),
                        str(event.get("source") or "import"),
                    ],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                legacy_label = str(legacy_id)
                if (
                    not legacy_label
                    or len(legacy_label) > 32
                    or any(
                        character
                        not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                        for character in legacy_label
                    )
                ):
                    legacy_label = hashlib.sha256(
                        legacy_label.encode("utf-8")
                    ).hexdigest()[:16]
                event_id = (
                    f"legacy_event_{legacy_label}_{hashlib.sha256(identity).hexdigest()}"
                )
            if backup_format == BACKUP_FORMAT and version == 1 and ":" in event_id:
                _, canonical_id = event_id.split(":", 1)
                if canonical_id:
                    event_id = canonical_id
            normalized = dict(event)
            normalized["client_event_id"] = event_id
            previous_event_ids = normalized.get("previous_event_ids", [])
            if previous_event_ids is None:
                previous_event_ids = []
            if not isinstance(previous_event_ids, list):
                raise ValueError("备份文件包含无效的旧稳定 ID")
            previous_event_ids = list(previous_event_ids)
            if backup_format == "vrchat-monitor-backup" and event.get("id") is not None:
                migrated_match = LEGACY_EVENT_ID_PATTERN.fullmatch(str(event_id))
                row_id = str(event["id"])
                if migrated_match and row_id == migrated_match.group(1):
                    legacy_bridge_id = f"local-{row_id}"
                    if legacy_bridge_id not in previous_event_ids:
                        previous_event_ids.append(legacy_bridge_id)
            normalized["previous_event_ids"] = previous_event_ids
            normalized_events.append(normalized)
        return self.ingest(
            tenant_id,
            "import",
            friends,
            normalized_events,
            "import",
            friend_batch_limit=None,
            event_batch_limit=None,
        )
