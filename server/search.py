from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from .storage import Store


USER_ID = re.compile(r"\busr_[A-Za-z0-9-]+\b")
WORLD_ID = re.compile(r"\bwrld_[A-Za-z0-9-]+\b")


@dataclass(frozen=True, slots=True)
class ParsedQuery:
    text: str
    user_id: str | None
    world_id: str | None


def parse_query(value: str) -> ParsedQuery:
    text = " ".join(str(value or "").strip().split())[:160]
    user = USER_ID.search(text)
    world = WORLD_ID.search(text)
    return ParsedQuery(
        text=text,
        user_id=user.group(0) if user else None,
        world_id=world.group(0) if world else None,
    )


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class SearchService:
    def __init__(self, store: Store):
        self.store = store

    def search(self, tenant_id: str, query: str, limit: int = 8) -> dict[str, Any]:
        parsed = parse_query(query)
        if not parsed.text:
            raise ValueError("请输入搜索内容")
        group_limit = max(1, min(int(limit), 20))
        with self.store.lock, self.store.connection() as db:
            self.store._require_tenant(db, tenant_id)
            groups = {
                "people": self._people(db, tenant_id, parsed, group_limit),
                "worlds": self._worlds(db, tenant_id, parsed, group_limit),
                "history": self._history(db, tenant_id, parsed, group_limit),
                "destinations": self._destinations(parsed.text, group_limit),
            }
        return {"query": parsed.text, "groups": groups}

    @staticmethod
    def _people(db: Any, tenant_id: str, parsed: ParsedQuery, limit: int) -> list[dict[str, Any]]:
        pattern = _like(parsed.text)
        rows = db.execute(
            """SELECT f.id,f.username,f.display_name,f.status,f.location,
            f.avatar_url,f.avatar_image_url,f.is_self,
            COALESCE(a.note,'') AS note,COALESCE(a.pinned,0) AS pinned
            FROM friends f LEFT JOIN friend_annotations a
              ON a.tenant_id=f.tenant_id AND a.friend_id=f.id
            WHERE f.tenant_id=?
            AND substr(f.id,1,4) NOT IN ('not_','frq_') AND (
                f.id=? OR f.username LIKE ? ESCAPE '\\'
                OR f.display_name LIKE ? ESCAPE '\\'
                OR a.note LIKE ? ESCAPE '\\'
                OR EXISTS(
                    SELECT 1 FROM friend_identity_events i
                    WHERE i.tenant_id=f.tenant_id AND i.friend_id=f.id
                      AND (i.old_value LIKE ? ESCAPE '\\' OR i.new_value LIKE ? ESCAPE '\\')
                )
                OR EXISTS(
                    SELECT 1 FROM friend_tags ft JOIN tags t
                      ON t.tenant_id=ft.tenant_id AND t.id=ft.tag_id
                    WHERE ft.tenant_id=f.tenant_id AND ft.friend_id=f.id
                      AND t.name LIKE ? ESCAPE '\\'
                )
            )
            ORDER BY CASE WHEN f.id=? THEN 0 WHEN a.pinned=1 THEN 1 ELSE 2 END,
            CASE WHEN f.status='offline' THEN 1 ELSE 0 END,
            f.display_name COLLATE NOCASE,f.id LIMIT ?""",
            (
                tenant_id,
                parsed.user_id or "",
                pattern,
                pattern,
                pattern,
                pattern,
                pattern,
                pattern,
                parsed.user_id or "",
                limit,
            ),
        ).fetchall()
        results: list[dict[str, Any]] = []
        folded = parsed.text.casefold()
        for row in rows:
            friend_id = str(row["id"])
            matches: list[str] = []
            if parsed.user_id == friend_id:
                matches.append("id")
            if folded in str(row["username"]).casefold() or folded in str(
                row["display_name"]
            ).casefold():
                matches.append("current_name")
            if folded and folded in str(row["note"]).casefold():
                matches.append("note")
            historical = db.execute(
                """SELECT 1 FROM friend_identity_events
                WHERE tenant_id=? AND friend_id=?
                  AND (old_value LIKE ? ESCAPE '\\' OR new_value LIKE ? ESCAPE '\\')
                LIMIT 1""",
                (tenant_id, friend_id, pattern, pattern),
            ).fetchone()
            if historical:
                matches.append("historical_name")
            tag_rows = db.execute(
                """SELECT t.id,t.name,t.color FROM friend_tags ft JOIN tags t
                  ON t.tenant_id=ft.tenant_id AND t.id=ft.tag_id
                WHERE ft.tenant_id=? AND ft.friend_id=?
                ORDER BY t.name COLLATE NOCASE""",
                (tenant_id, friend_id),
            ).fetchall()
            tags = [dict(item) for item in tag_rows]
            if any(folded in str(tag["name"]).casefold() for tag in tags):
                matches.append("tag")
            results.append(
                {
                    "id": friend_id,
                    "username": str(row["username"]),
                    "name": str(row["display_name"]),
                    "status": str(row["status"]),
                    "location": str(row["location"]),
                    "avatar_url": str(row["avatar_url"] or row["avatar_image_url"] or ""),
                    "is_self": bool(row["is_self"]),
                    "pinned": bool(row["pinned"]),
                    "tags": tags,
                    "matches": list(dict.fromkeys(matches)),
                    "href": f"#view=people&personDetail={quote(friend_id)}",
                }
            )
        return results

    @staticmethod
    def _worlds(db: Any, tenant_id: str, parsed: ParsedQuery, limit: int) -> list[dict[str, Any]]:
        rows = db.execute(
            """WITH observed AS (
                SELECT CASE
                    WHEN instr(location,':')>0 THEN substr(location,1,instr(location,':')-1)
                    ELSE location END AS world_id,
                    MAX(occurred_at) AS last_observed
                FROM status_events
                WHERE tenant_id=? AND location GLOB 'wrld_*'
                GROUP BY world_id
            )
            SELECT o.world_id,o.last_observed,w.payload_json,w.fetched_at
            FROM observed o LEFT JOIN world_cache w ON w.world_id=o.world_id
            ORDER BY CASE WHEN o.world_id=? THEN 0 ELSE 1 END,
            o.last_observed DESC,o.world_id LIMIT 2000""",
            (tenant_id, parsed.world_id or ""),
        ).fetchall()
        folded = parsed.text.casefold()
        results: list[dict[str, Any]] = []
        for row in rows:
            world_id = str(row["world_id"])
            try:
                payload = json.loads(str(row["payload_json"] or "{}"))
            except json.JSONDecodeError:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            searchable = " ".join(
                (
                    world_id,
                    str(payload.get("name") or ""),
                    str(payload.get("author_name") or payload.get("authorName") or ""),
                    str(payload.get("description") or ""),
                )
            ).casefold()
            if parsed.world_id != world_id and folded not in searchable:
                continue
            results.append(
                {
                    "id": world_id,
                    "name": str(payload.get("name") or world_id),
                    "author_name": str(
                        payload.get("author_name") or payload.get("authorName") or ""
                    ),
                    "thumbnail_url": str(
                        payload.get("thumbnail_url")
                        or payload.get("thumbnailImageUrl")
                        or ""
                    ),
                    "last_observed": str(row["last_observed"]),
                    "resolved": bool(row["payload_json"]),
                    "href": f"#view=worlds&worldDetail={quote(world_id)}",
                }
            )
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _history(db: Any, tenant_id: str, parsed: ParsedQuery, limit: int) -> list[dict[str, Any]]:
        pattern = _like(parsed.text)
        rows = db.execute(
            """SELECT e.client_event_id,e.friend_id,e.occurred_at,e.old_status,
            e.new_status,e.location,e.platform,f.display_name,f.username
            FROM status_events e JOIN friends f
              ON f.tenant_id=e.tenant_id AND f.id=e.friend_id
            WHERE e.tenant_id=?
            AND substr(e.friend_id,1,4) NOT IN ('not_','frq_') AND (
                e.client_event_id=? OR e.friend_id=?
                OR f.display_name LIKE ? ESCAPE '\\'
                OR f.username LIKE ? ESCAPE '\\'
                OR e.new_status LIKE ? ESCAPE '\\'
                OR e.location LIKE ? ESCAPE '\\'
            )
            ORDER BY e.occurred_at DESC,e.client_event_id DESC LIMIT ?""",
            (
                tenant_id,
                parsed.text,
                parsed.user_id or "",
                pattern,
                pattern,
                pattern,
                pattern,
                limit,
            ),
        ).fetchall()
        return [
            {
                "id": str(row["client_event_id"]),
                "friend_id": str(row["friend_id"]),
                "name": str(row["display_name"]),
                "username": str(row["username"]),
                "occurred_at": str(row["occurred_at"]),
                "old_status": str(row["old_status"]),
                "new_status": str(row["new_status"]),
                "location": str(row["location"]),
                "platform": str(row["platform"]),
                "href": (
                    "#area=more&section=history&historyQ="
                    f"{quote(parsed.text, safe='')}"
                ),
            }
            for row in rows
        ]

    @staticmethod
    def _destinations(query: str, limit: int) -> list[dict[str, str]]:
        folded = query.casefold()
        destinations = (
            ("在线", "现在在线的玩家", "overview", "在线 live now"),
            ("玩家", "所有玩家、备注与标签", "people", "玩家 people friends notes tags"),
            ("分析", "时间轴与活动规律", "daily", "分析 analytics timeline heatmap"),
            ("世界", "去过的世界与发现", "worlds", "世界 worlds discovery"),
            ("历史", "全部状态变化", "history", "历史 history events"),
            ("更多", "设置、备份与恢复", "data", "更多 settings backup restore"),
        )
        return [
            {
                "id": view,
                "name": name,
                "description": description,
                "href": f"#view={view}",
            }
            for name, description, view, terms in destinations
            if folded in terms.casefold()
        ][:limit]
