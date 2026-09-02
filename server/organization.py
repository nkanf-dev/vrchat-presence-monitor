from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .storage import Store, now


class OrganizationNotFound(KeyError):
    pass


class OrganizationConflict(RuntimeError):
    pass


class AnnotationConflict(OrganizationConflict):
    def __init__(self, server: dict[str, Any]):
        super().__init__("annotation changed")
        self.server = server


class DashboardConflict(OrganizationConflict):
    def __init__(self, server: dict[str, Any]):
        super().__init__("dashboard changed")
        self.server = server


def default_dashboard_document() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "title": "我的仪表盘",
        "range_days": 7,
        "refresh_seconds": 60,
        "panels": [
            {"id": "online-now", "kind": "online-now", "title": "当前在线", "x": 0, "y": 0, "w": 3, "h": 4, "range_days": 0, "limit": 10, "include_self": True},
            {"id": "tracked-count", "kind": "tracked-count", "title": "追踪人数", "x": 3, "y": 0, "w": 3, "h": 4, "range_days": 0, "limit": 10, "include_self": True},
            {"id": "status-breakdown", "kind": "status-breakdown", "title": "当前状态", "x": 6, "y": 0, "w": 6, "h": 7, "range_days": 0, "limit": 10, "include_self": True},
            {"id": "online-ranking", "kind": "online-ranking", "title": "在线时长排行", "x": 0, "y": 4, "w": 6, "h": 8, "range_days": 0, "limit": 10, "include_self": True},
            {"id": "daily-changes", "kind": "daily-changes", "title": "每日状态变化", "x": 6, "y": 7, "w": 6, "h": 5, "range_days": 0, "limit": 10, "include_self": True},
            {"id": "friend-heatmap", "kind": "friend-heatmap", "title": "好友时段热力", "x": 0, "y": 12, "w": 12, "h": 9, "range_days": 0, "limit": 12, "include_self": True},
            {"id": "world-ranking", "kind": "world-ranking", "title": "热门世界", "x": 0, "y": 21, "w": 12, "h": 7, "range_days": 30, "limit": 10, "include_self": True},
        ],
    }


@dataclass(frozen=True, slots=True)
class Annotation:
    friend_id: str
    note: str
    pinned: bool
    revision: str | None
    updated_at: str | None

    def payload(self, tags: list[dict[str, Any]]) -> dict[str, Any]:
        return {**asdict(self), "tags": tags}


class OrganizationService:
    def __init__(self, store: Store):
        self.store = store

    @staticmethod
    def _require_friend(
        db: sqlite3.Connection, tenant_id: str, friend_id: str
    ) -> None:
        if db.execute(
            "SELECT 1 FROM friends WHERE tenant_id=? AND id=?",
            (tenant_id, friend_id),
        ).fetchone() is None:
            raise OrganizationNotFound("friend not found")

    @staticmethod
    def _annotation(
        db: sqlite3.Connection, tenant_id: str, friend_id: str
    ) -> Annotation | None:
        row = db.execute(
            """SELECT friend_id,note,pinned,revision,updated_at
            FROM friend_annotations WHERE tenant_id=? AND friend_id=?""",
            (tenant_id, friend_id),
        ).fetchone()
        if row is None:
            return None
        return Annotation(
            friend_id=str(row["friend_id"]),
            note=str(row["note"]),
            pinned=bool(row["pinned"]),
            revision=str(row["revision"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _friend_tags(
        db: sqlite3.Connection, tenant_id: str, friend_id: str
    ) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in db.execute(
                """SELECT t.id,t.name,t.color,t.created_at,t.updated_at
                FROM friend_tags ft JOIN tags t
                  ON t.tenant_id=ft.tenant_id AND t.id=ft.tag_id
                WHERE ft.tenant_id=? AND ft.friend_id=?
                ORDER BY t.name COLLATE NOCASE,t.id""",
                (tenant_id, friend_id),
            ).fetchall()
        ]

    def get_annotation(self, tenant_id: str, friend_id: str) -> dict[str, Any]:
        with self.store.lock, self.store.connection() as db:
            self._require_friend(db, tenant_id, friend_id)
            annotation = self._annotation(db, tenant_id, friend_id) or Annotation(
                friend_id=friend_id,
                note="",
                pinned=False,
                revision=None,
                updated_at=None,
            )
            return annotation.payload(self._friend_tags(db, tenant_id, friend_id))

    def put_annotation(
        self,
        tenant_id: str,
        friend_id: str,
        note: str,
        pinned: bool,
        expected_revision: str | None,
    ) -> dict[str, Any]:
        normalized_note = str(note)
        if len(normalized_note) > 20_000:
            raise ValueError("备注过长")
        with self.store.lock, self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_friend(db, tenant_id, friend_id)
            current = self._annotation(db, tenant_id, friend_id)
            if current is not None and expected_revision != current.revision:
                raise AnnotationConflict(
                    current.payload(self._friend_tags(db, tenant_id, friend_id))
                )
            if current is None and expected_revision is not None:
                raise AnnotationConflict(
                    Annotation(friend_id, "", False, None, None).payload([])
                )
            revision = secrets.token_urlsafe(18)
            stamp = now()
            db.execute(
                """INSERT INTO friend_annotations(
                    tenant_id,friend_id,note,pinned,revision,updated_at
                ) VALUES(?,?,?,?,?,?) ON CONFLICT(tenant_id,friend_id) DO UPDATE SET
                    note=excluded.note,pinned=excluded.pinned,
                    revision=excluded.revision,updated_at=excluded.updated_at""",
                (
                    tenant_id,
                    friend_id,
                    normalized_note,
                    int(bool(pinned)),
                    revision,
                    stamp,
                ),
            )
            return Annotation(
                friend_id, normalized_note, bool(pinned), revision, stamp
            ).payload(self._friend_tags(db, tenant_id, friend_id))

    @staticmethod
    def _normalize_tag_name(value: str) -> str:
        name = " ".join(str(value or "").strip().split())
        if not name or len(name) > 80:
            raise ValueError("标签名称长度应为 1–80 个字符")
        return name

    @staticmethod
    def _normalize_color(value: str) -> str:
        color = str(value or "").strip().lower()
        if (
            len(color) != 7
            or not color.startswith("#")
            or any(character not in "0123456789abcdef" for character in color[1:])
        ):
            raise ValueError("标签颜色格式无效")
        return color

    @staticmethod
    def _assert_unique_tag_name(
        db: sqlite3.Connection,
        tenant_id: str,
        name: str,
        *,
        except_id: str | None = None,
    ) -> None:
        folded = name.casefold()
        for row in db.execute(
            "SELECT id,name FROM tags WHERE tenant_id=?", (tenant_id,)
        ).fetchall():
            if str(row["id"]) != except_id and str(row["name"]).casefold() == folded:
                raise OrganizationConflict("tag name already exists")

    def list_tags(self, tenant_id: str) -> list[dict[str, Any]]:
        with self.store.lock, self.store.connection() as db:
            self.store._require_tenant(db, tenant_id)
            return [
                dict(row)
                for row in db.execute(
                    """SELECT t.id,t.name,t.color,t.created_at,t.updated_at,
                    COUNT(ft.friend_id) AS friend_count
                    FROM tags t LEFT JOIN friend_tags ft
                      ON ft.tenant_id=t.tenant_id AND ft.tag_id=t.id
                    WHERE t.tenant_id=? GROUP BY t.id
                    ORDER BY t.name COLLATE NOCASE,t.id""",
                    (tenant_id,),
                ).fetchall()
            ]

    def create_tag(self, tenant_id: str, name: str, color: str) -> dict[str, Any]:
        normalized_name = self._normalize_tag_name(name)
        normalized_color = self._normalize_color(color)
        with self.store.lock, self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.store._require_tenant(db, tenant_id)
            self._assert_unique_tag_name(db, tenant_id, normalized_name)
            tag_id = "tag_" + secrets.token_urlsafe(12)
            stamp = now()
            db.execute(
                """INSERT INTO tags(tenant_id,id,name,color,created_at,updated_at)
                VALUES(?,?,?,?,?,?)""",
                (
                    tenant_id,
                    tag_id,
                    normalized_name,
                    normalized_color,
                    stamp,
                    stamp,
                ),
            )
            return {
                "id": tag_id,
                "name": normalized_name,
                "color": normalized_color,
                "created_at": stamp,
                "updated_at": stamp,
                "friend_count": 0,
            }

    def update_tag(
        self, tenant_id: str, tag_id: str, name: str, color: str
    ) -> dict[str, Any]:
        normalized_name = self._normalize_tag_name(name)
        normalized_color = self._normalize_color(color)
        with self.store.lock, self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT created_at FROM tags WHERE tenant_id=? AND id=?",
                (tenant_id, tag_id),
            ).fetchone()
            if current is None:
                raise OrganizationNotFound("tag not found")
            self._assert_unique_tag_name(
                db, tenant_id, normalized_name, except_id=tag_id
            )
            stamp = now()
            db.execute(
                """UPDATE tags SET name=?,color=?,updated_at=?
                WHERE tenant_id=? AND id=?""",
                (normalized_name, normalized_color, stamp, tenant_id, tag_id),
            )
            count = db.execute(
                """SELECT COUNT(*) FROM friend_tags
                WHERE tenant_id=? AND tag_id=?""",
                (tenant_id, tag_id),
            ).fetchone()[0]
            return {
                "id": tag_id,
                "name": normalized_name,
                "color": normalized_color,
                "created_at": str(current["created_at"]),
                "updated_at": stamp,
                "friend_count": int(count),
            }

    def delete_tag(self, tenant_id: str, tag_id: str) -> bool:
        with self.store.lock, self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "DELETE FROM tags WHERE tenant_id=? AND id=?", (tenant_id, tag_id)
            )
            if cursor.rowcount == 0:
                raise OrganizationNotFound("tag not found")
            return True

    def assign_tag(self, tenant_id: str, friend_id: str, tag_id: str) -> dict[str, Any]:
        with self.store.lock, self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_friend(db, tenant_id, friend_id)
            if db.execute(
                "SELECT 1 FROM tags WHERE tenant_id=? AND id=?",
                (tenant_id, tag_id),
            ).fetchone() is None:
                raise OrganizationNotFound("tag not found")
            db.execute(
                """INSERT OR IGNORE INTO friend_tags(
                    tenant_id,friend_id,tag_id,created_at
                ) VALUES(?,?,?,?)""",
                (tenant_id, friend_id, tag_id, now()),
            )
            return {"ok": True, "friend_id": friend_id, "tag_id": tag_id}

    def unassign_tag(
        self, tenant_id: str, friend_id: str, tag_id: str
    ) -> dict[str, Any]:
        with self.store.lock, self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_friend(db, tenant_id, friend_id)
            db.execute(
                """DELETE FROM friend_tags
                WHERE tenant_id=? AND friend_id=? AND tag_id=?""",
                (tenant_id, friend_id, tag_id),
            )
            return {"ok": True, "friend_id": friend_id, "tag_id": tag_id}

    def get_preferences(self, tenant_id: str) -> dict[str, str]:
        with self.store.lock, self.store.connection() as db:
            self.store._require_tenant(db, tenant_id)
            row = db.execute(
                "SELECT timezone,updated_at FROM tenant_preferences WHERE tenant_id=?",
                (tenant_id,),
            ).fetchone()
            return {
                "timezone": str(row["timezone"]) if row else "Asia/Shanghai",
                "updated_at": str(row["updated_at"]) if row else "",
            }

    def put_preferences(self, tenant_id: str, timezone_name: str) -> dict[str, str]:
        normalized = str(timezone_name or "").strip()
        try:
            ZoneInfo(normalized)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("请选择有效时区") from error
        stamp = now()
        with self.store.lock, self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.store._require_tenant(db, tenant_id)
            db.execute(
                """INSERT INTO tenant_preferences(tenant_id,timezone,updated_at)
                VALUES(?,?,?) ON CONFLICT(tenant_id) DO UPDATE SET
                timezone=excluded.timezone,updated_at=excluded.updated_at""",
                (tenant_id, normalized, stamp),
            )
        return {"timezone": normalized, "updated_at": stamp}

    @staticmethod
    def _dashboard_payload(row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            return {
                "revision": None,
                "updated_at": "",
                "document": default_dashboard_document(),
            }
        return {
            "revision": str(row["revision"]),
            "updated_at": str(row["updated_at"]),
            "document": json.loads(str(row["document_json"])),
        }

    def get_dashboard(self, tenant_id: str) -> dict[str, Any]:
        with self.store.lock, self.store.connection() as db:
            self.store._require_tenant(db, tenant_id)
            row = db.execute(
                "SELECT document_json,revision,updated_at FROM dashboard_configs WHERE tenant_id=?",
                (tenant_id,),
            ).fetchone()
            return self._dashboard_payload(row)

    def put_dashboard(
        self,
        tenant_id: str,
        document: dict[str, Any],
        expected_revision: str | None,
    ) -> dict[str, Any]:
        encoded = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(encoded.encode("utf-8")) > 64 * 1024:
            raise ValueError("仪表盘配置过大")
        stamp = now()
        revision = secrets.token_urlsafe(18)
        with self.store.lock, self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.store._require_tenant(db, tenant_id)
            current = db.execute(
                "SELECT document_json,revision,updated_at FROM dashboard_configs WHERE tenant_id=?",
                (tenant_id,),
            ).fetchone()
            current_payload = self._dashboard_payload(current)
            if current is not None and expected_revision != current_payload["revision"]:
                raise DashboardConflict(current_payload)
            if current is None and expected_revision is not None:
                raise DashboardConflict(current_payload)
            db.execute(
                """INSERT INTO dashboard_configs(tenant_id,document_json,revision,updated_at)
                VALUES(?,?,?,?) ON CONFLICT(tenant_id) DO UPDATE SET
                document_json=excluded.document_json,
                revision=excluded.revision,
                updated_at=excluded.updated_at""",
                (tenant_id, encoded, revision, stamp),
            )
        return {"revision": revision, "updated_at": stamp, "document": document}
