from __future__ import annotations

import copy
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import date as date_type
from datetime import datetime, time as time_type, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from vrchat_monitor.vrchat import USER_AGENT, VRChatError, world_id_from_location

from .storage import Store


WORLD_IMAGE_LIMIT = 8 * 1024 * 1024
WORLD_IMAGE_TYPES = {
    "image/avif",
    "image/gif",
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_date(value: str | None, label: str) -> date_type | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as error:
        raise ValueError(f"{label}必须是 YYYY-MM-DD") from error


class AnalyticsService:
    """Tenant-scoped analytics compatible with the mature local database API."""

    def __init__(self, store: Store, timezone_name: str = "Asia/Shanghai"):
        self.store = store
        self.zone = ZoneInfo(timezone_name)
        self._cache_lock = threading.RLock()
        self._presence_cache: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
        self._world_cache: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}

    @staticmethod
    def _day_bounds(
        day: date_type,
        zone: ZoneInfo,
        current: datetime,
    ) -> tuple[datetime, datetime]:
        start = datetime.combine(day, time_type.min, tzinfo=zone)
        end = datetime.combine(day + timedelta(days=1), time_type.min, tzinfo=zone)
        if day == current.date():
            end = min(end, current)
        return start, end

    @staticmethod
    def _effective_state(status: str, location: str, is_self: bool = False) -> str:
        normalized_status = str(status or "offline").strip().lower() or "offline"
        normalized_location = str(location or "").strip().lower()
        if normalized_location == "offline":
            return "offline"
        if is_self and not normalized_location:
            return "offline"
        return normalized_status

    @classmethod
    def _online_spans(
        cls,
        events: list[dict[str, Any]],
        start: datetime,
        end: datetime,
        zone: ZoneInfo,
        is_self: bool = False,
    ) -> list[dict[str, Any]]:
        if end <= start:
            return []
        state = "offline"
        cursor = start
        spans: list[dict[str, Any]] = []
        for event in events:
            occurred = _parse_time(event.get("occurred_at"))
            if occurred is None:
                continue
            occurred = occurred.astimezone(zone)
            new_state = cls._effective_state(
                str(event.get("new_status") or "offline"),
                str(event.get("location") or ""),
                is_self,
            )
            if occurred < start:
                state = new_state
                continue
            if occurred >= end:
                break
            if state != "offline" and occurred > cursor:
                spans.append(
                    {
                        "start_minute": round((cursor - start).total_seconds() / 60),
                        "end_minute": round((occurred - start).total_seconds() / 60),
                        "status": state,
                    }
                )
            state = new_state
            cursor = max(cursor, occurred)
        if state != "offline" and end > cursor:
            spans.append(
                {
                    "start_minute": round((cursor - start).total_seconds() / 60),
                    "end_minute": round((end - start).total_seconds() / 60),
                    "status": state,
                }
            )
        return [span for span in spans if span["end_minute"] > span["start_minute"]]

    @classmethod
    def _online_seconds(
        cls,
        events: list[dict[str, Any]],
        start: datetime,
        end: datetime,
        is_self: bool,
    ) -> float:
        if end <= start:
            return 0.0
        state = "offline"
        cursor = start
        seconds = 0.0
        for event in events:
            occurred = _parse_time(event.get("occurred_at"))
            if occurred is None:
                continue
            occurred = occurred.astimezone(timezone.utc)
            new_state = cls._effective_state(
                str(event.get("new_status") or "offline"),
                str(event.get("location") or ""),
                is_self,
            )
            if occurred < start:
                state = new_state
                continue
            if occurred >= end:
                break
            if state != "offline" and occurred > cursor:
                seconds += (occurred - cursor).total_seconds()
            state = new_state
            cursor = max(cursor, occurred)
        if state != "offline" and end > cursor:
            seconds += (end - cursor).total_seconds()
        return max(0.0, seconds)

    @classmethod
    def _world_spans(
        cls,
        events: list[dict[str, Any]],
        start: datetime,
        end: datetime,
        zone: ZoneInfo,
        is_self: bool = False,
    ) -> list[dict[str, Any]]:
        if end <= start:
            return []
        state = "offline"
        location = ""
        platform = ""
        cursor = start
        spans: list[dict[str, Any]] = []
        for event in events:
            occurred = _parse_time(event.get("occurred_at"))
            if occurred is None:
                continue
            occurred = occurred.astimezone(zone)
            event_location = str(event.get("location") or "")
            event_platform = str(event.get("platform") or "")
            new_state = cls._effective_state(
                str(event.get("new_status") or "offline"),
                event_location,
                is_self,
            )
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
                spans.append(
                    {
                        "start_minute": round((cursor - start).total_seconds() / 60),
                        "end_minute": round((occurred - start).total_seconds() / 60),
                        "status": state,
                        "location": location,
                        "world_id": world_id_from_location(location) or "",
                        "platform": platform,
                    }
                )
            state = new_state
            if event_location:
                location = event_location
            if event_platform:
                platform = event_platform
            cursor = max(cursor, occurred)
        if state != "offline" and end > cursor:
            spans.append(
                {
                    "start_minute": round((cursor - start).total_seconds() / 60),
                    "end_minute": round((end - start).total_seconds() / 60),
                    "status": state,
                    "location": location,
                    "world_id": world_id_from_location(location) or "",
                    "platform": platform,
                }
            )
        return [span for span in spans if span["end_minute"] > span["start_minute"]]

    def _snapshot(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        *,
        avatar: bool = False,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], tuple[int, str]]:
        start_key = start.astimezone(timezone.utc).isoformat(timespec="microseconds")
        end_key = end.astimezone(timezone.utc).isoformat(timespec="microseconds")
        avatar_column = ",avatar_url" if avatar else ""
        event_columns = (
            "client_event_id,friend_id,occurred_at,old_status,new_status,"
            "location,platform,source"
        )
        with self.store.lock, self.store.connection() as db:
            self.store._require_tenant(db, tenant_id)
            revision_row = db.execute(
                """SELECT COALESCE(MAX(rowid),0) AS event_revision,
                COALESCE((SELECT MAX(updated_at) FROM friends WHERE tenant_id=?),'') AS friend_revision
                FROM status_events WHERE tenant_id=?""",
                (tenant_id, tenant_id),
            ).fetchone()
            friends = [
                dict(row)
                for row in db.execute(
                    f"""SELECT id,username,display_name,is_self,status,location{avatar_column}
                    FROM friends WHERE tenant_id=?
                    ORDER BY is_self DESC,CASE WHEN status='offline' THEN 1 ELSE 0 END,
                    display_name COLLATE NOCASE""",
                    (tenant_id,),
                ).fetchall()
            ]
            events = [
                dict(row)
                for row in db.execute(
                    f"""WITH prior AS (
                        SELECT {event_columns},ROW_NUMBER() OVER (
                            PARTITION BY friend_id
                            ORDER BY occurred_at DESC,client_event_id DESC
                        ) AS rank
                        FROM status_events
                        WHERE tenant_id=? AND occurred_at<?
                    ),windowed AS (
                        SELECT {event_columns}
                        FROM status_events
                        WHERE tenant_id=? AND occurred_at>=? AND occurred_at<?
                    )
                    SELECT {event_columns} FROM windowed
                    UNION ALL
                    SELECT {event_columns} FROM prior WHERE rank=1
                    ORDER BY friend_id,occurred_at,client_event_id""",
                    (tenant_id, start_key, tenant_id, start_key, end_key),
                ).fetchall()
            ]
        revision = (
            int(revision_row["event_revision"] or 0),
            str(revision_row["friend_revision"] or ""),
        )
        return friends, events, revision

    @staticmethod
    def _group_events(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in events:
            grouped[str(event["friend_id"])].append(event)
        return grouped

    def stats(self, tenant_id: str, days: int = 30) -> dict[str, Any]:
        days = max(1, min(int(days), 90))
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        friends, events, _ = self._snapshot(tenant_id, start, end)
        grouped = self._group_events(events)
        totals: list[dict[str, Any]] = []
        online_now = 0
        for friend in friends:
            is_self = bool(friend["is_self"])
            if self._effective_state(friend["status"], friend["location"], is_self) != "offline":
                online_now += 1
            seconds = self._online_seconds(
                grouped.get(str(friend["id"]), []), start, end, is_self
            )
            totals.append(
                {
                    "id": str(friend["id"]),
                    "name": str(friend["display_name"]),
                    "seconds": round(seconds),
                    "hours": round(seconds / 3600, 1),
                }
            )

        status_counts: Counter[str] = Counter()
        daily_changes: Counter[str] = Counter()
        for event in events:
            occurred = _parse_time(event.get("occurred_at"))
            if occurred is None or occurred < start:
                continue
            status_counts[str(event.get("new_status") or "offline")] += 1
            daily_changes[occurred.astimezone(self.zone).date().isoformat()] += 1
        ordered = sorted(totals, key=lambda item: item["seconds"], reverse=True)
        return {
            "days": days,
            "online_now": online_now,
            "friend_count": len(friends),
            "status_counts": dict(status_counts),
            "daily_changes": [
                {"day": day, "changes": daily_changes[day]}
                for day in sorted(daily_changes)
            ],
            "online_hours": ordered[:10],
            "online_hours_all": ordered,
        }

    def presence_overview(
        self,
        tenant_id: str,
        day: str | None = None,
        days: int = 30,
        heatmap_from: str | None = None,
        heatmap_to: str | None = None,
    ) -> dict[str, Any]:
        current = datetime.now(self.zone)
        today = current.date()
        selected = _parse_date(day, "日期") or today
        future_clamped = selected > today
        selected = min(selected, today)
        legacy_days = max(7, min(int(days), 90))
        range_start = _parse_date(heatmap_from, "热力图起始日期")
        range_end = _parse_date(heatmap_to, "热力图结束日期") or today
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

        query_start = datetime.combine(
            min(selected, range_start), time_type.min, tzinfo=self.zone
        )
        query_end = self._day_bounds(max(selected, range_end), self.zone, current)[1]
        friends, events, revision = self._snapshot(tenant_id, query_start, query_end)
        cache_key = (
            tenant_id,
            selected.isoformat(),
            range_start.isoformat(),
            range_end.isoformat(),
            *revision,
        )
        cache_is_stable = selected < today and range_end < today
        with self._cache_lock:
            cached = self._presence_cache.get(cache_key)
            if cached and (cache_is_stable or time.monotonic() - cached[0] < 15):
                result = copy.deepcopy(cached[1])
                result["future_clamped"] = future_clamped
                return result

        grouped = self._group_events(events)
        day_start, day_end = self._day_bounds(selected, self.zone, current)
        timeline: list[dict[str, Any]] = []
        heatmap: list[dict[str, Any]] = []
        completed_days = sum(
            1
            for offset in range(range_days)
            if range_start + timedelta(days=offset) < today
        )
        observed_minutes = [0.0] * 24
        for offset in range(range_days):
            observed_day = range_start + timedelta(days=offset)
            observed_start, observed_end = self._day_bounds(
                observed_day, self.zone, current
            )
            for hour in range(24):
                hour_start = observed_start + timedelta(hours=hour)
                hour_end = min(hour_start + timedelta(hours=1), observed_end)
                observed_minutes[hour] += max(
                    0.0, (hour_end - hour_start).total_seconds() / 60
                )
        for friend in friends:
            friend_id = str(friend["id"])
            friend_events = grouped.get(friend_id, [])
            is_self = bool(friend["is_self"])
            selected_spans = self._online_spans(
                friend_events, day_start, day_end, self.zone, is_self
            )
            timeline.append(
                {
                    "id": friend_id,
                    "name": str(friend["display_name"]),
                    "username": str(friend["username"]),
                    "is_self": is_self,
                    "spans": selected_spans,
                    "online_minutes": round(
                        sum(
                            span["end_minute"] - span["start_minute"]
                            for span in selected_spans
                        ),
                        1,
                    ),
                }
            )
            total_minutes = [0.0] * 24
            for offset in range(range_days):
                current_day = range_start + timedelta(days=offset)
                current_start, current_end = self._day_bounds(
                    current_day, self.zone, current
                )
                spans = self._online_spans(
                    friend_events, current_start, current_end, self.zone, is_self
                )
                for hour in range(24):
                    hour_start = hour * 60
                    hour_end = (hour + 1) * 60
                    for span in spans:
                        total_minutes[hour] += max(
                            0,
                            min(span["end_minute"], hour_end)
                            - max(span["start_minute"], hour_start),
                        )
            heatmap.append(
                {
                    "id": friend_id,
                    "name": str(friend["display_name"]),
                    "values": [
                        round(value / observed_minutes[hour], 3)
                        if observed_minutes[hour] > 0
                        else 0.0
                        for hour, value in enumerate(total_minutes)
                    ],
                }
            )

        result = {
            "day": selected.isoformat(),
            "days": range_days,
            "future_clamped": future_clamped,
            "heatmap_from": range_start.isoformat(),
            "heatmap_to": range_end.isoformat(),
            "heatmap_days": range_days,
            "heatmap_complete_days": completed_days,
            "heatmap_observed_minutes": [round(value, 1) for value in observed_minutes],
            "timezone": self.zone.key,
            "timeline": timeline,
            "heatmap": heatmap,
        }
        with self._cache_lock:
            if len(self._presence_cache) > 128:
                self._presence_cache.clear()
            self._presence_cache[cache_key] = (time.monotonic(), result)
        return copy.deepcopy(result)

    def world_presence_overview(
        self, tenant_id: str, day: str | None = None
    ) -> dict[str, Any]:
        current = datetime.now(self.zone)
        today = current.date()
        selected = _parse_date(day, "日期") or today
        future_clamped = selected > today
        selected = min(selected, today)
        day_start, day_end = self._day_bounds(selected, self.zone, current)
        friends, events, revision = self._snapshot(
            tenant_id, day_start, day_end, avatar=True
        )
        cache_key = (tenant_id, selected.isoformat(), *revision)
        cache_is_stable = selected < today
        with self._cache_lock:
            cached = self._world_cache.get(cache_key)
            if cached and (cache_is_stable or time.monotonic() - cached[0] < 15):
                result = copy.deepcopy(cached[1])
                result["future_clamped"] = future_clamped
                return result

        grouped = self._group_events(events)
        rows: list[dict[str, Any]] = []
        world_ids: set[str] = set()
        for friend in friends:
            friend_id = str(friend["id"])
            spans = self._world_spans(
                grouped.get(friend_id, []),
                day_start,
                day_end,
                self.zone,
                bool(friend["is_self"]),
            )
            world_ids.update(span["world_id"] for span in spans if span["world_id"])
            rows.append(
                {
                    "id": friend_id,
                    "name": str(friend["display_name"]),
                    "username": str(friend["username"]),
                    "is_self": bool(friend["is_self"]),
                    "avatar_url": str(friend.get("avatar_url") or ""),
                    "online_minutes": round(
                        sum(
                            span["end_minute"] - span["start_minute"]
                            for span in spans
                        ),
                        1,
                    ),
                    "spans": spans,
                }
            )
        result = {
            "day": selected.isoformat(),
            "future_clamped": future_clamped,
            "timezone": self.zone.key,
            "self_id": next((row["id"] for row in rows if row["is_self"]), ""),
            "friends": rows,
            "world_ids": sorted(world_ids),
        }
        with self._cache_lock:
            if len(self._world_cache) > 128:
                self._world_cache.clear()
            self._world_cache[cache_key] = (time.monotonic(), result)
        return copy.deepcopy(result)


def _trusted_image_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(str(url or "").strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    trusted = any(
        host == suffix or host.endswith(f".{suffix}")
        for suffix in ("vrchat.cloud", "vrcdn.cloud")
    )
    if (
        parsed.scheme != "https"
        or not trusted
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.fragment
    ):
        raise ValueError("世界缩略图地址不受信任")
    return urllib.parse.urlunsplit(parsed)


class _TrustedImageRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(
            req, fp, code, msg, headers, _trusted_image_url(newurl)
        )


def fetch_world_image(url: str) -> tuple[bytes, str]:
    trusted_url = _trusted_image_url(url)
    request = urllib.request.Request(
        trusted_url,
        headers={"User-Agent": USER_AGENT, "Accept": "image/avif,image/webp,image/*"},
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler(urllib.request.getproxies()),
        _TrustedImageRedirect(),
    )
    try:
        with opener.open(request, timeout=15) as response:
            _trusted_image_url(response.geturl())
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > WORLD_IMAGE_LIMIT:
                raise VRChatError("世界缩略图过大", 413)
            content_type = response.headers.get_content_type().lower()
            if content_type not in WORLD_IMAGE_TYPES:
                raise VRChatError("世界缩略图格式异常", 415)
            body = response.read(WORLD_IMAGE_LIMIT + 1)
            if len(body) > WORLD_IMAGE_LIMIT:
                raise VRChatError("世界缩略图过大", 413)
            return body, content_type
    except urllib.error.HTTPError as error:
        raise VRChatError("世界缩略图暂时不可用", error.code) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise VRChatError("世界缩略图暂时不可用") from error
