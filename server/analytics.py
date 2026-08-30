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

from vrchat_monitor.vrchat import USER_AGENT, VRChatError

from .observation import (
    TimeWindow,
    activity_cell,
    build_observed_windows,
    build_online_spans,
    build_tracking_windows,
    build_world_spans,
    coverage_summary,
    effective_state,
    intersect_windows,
)
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
    def _hour_window(
        day: date_type,
        hour: int,
        zone: ZoneInfo,
        current: datetime,
    ) -> TimeWindow | None:
        start = datetime.combine(day, time_type(hour=hour), tzinfo=zone)
        if hour == 23:
            end = datetime.combine(day + timedelta(days=1), time_type.min, tzinfo=zone)
        else:
            end = datetime.combine(day, time_type(hour=hour + 1), tzinfo=zone)
        if day == current.date():
            end = min(end, current)
        if end.astimezone(timezone.utc) <= start.astimezone(timezone.utc):
            return None
        return TimeWindow(start, end)

    @staticmethod
    def _window_payload(window: TimeWindow) -> dict[str, Any]:
        return {
            "start": window.start.isoformat(timespec="microseconds"),
            "end": window.end.isoformat(timespec="microseconds"),
            "minutes": round(window.minutes, 1),
        }

    @classmethod
    def _coverage_payload(
        cls,
        covered: list[TimeWindow],
        start: datetime,
        end: datetime,
    ) -> dict[str, Any]:
        summary = coverage_summary(covered, range_start=start, range_end=end)
        return {
            "expected_minutes": round(summary.expected_minutes, 1),
            "observed_minutes": round(summary.observed_minutes, 1),
            "ratio": round(summary.ratio, 4),
            "first_observed": summary.first_observed.isoformat(timespec="microseconds")
            if summary.first_observed
            else None,
            "last_observed": summary.last_observed.isoformat(timespec="microseconds")
            if summary.last_observed
            else None,
            "gaps": [cls._window_payload(gap) for gap in summary.gaps],
        }

    @staticmethod
    def _effective_state(status: str, location: str, is_self: bool = False) -> str:
        return effective_state(status, location, is_self)

    @classmethod
    def _online_spans(
        cls,
        events: list[dict[str, Any]],
        start: datetime,
        end: datetime,
        zone: ZoneInfo,
        is_self: bool = False,
        covered: list[TimeWindow] | None = None,
    ) -> list[dict[str, Any]]:
        if end <= start:
            return []
        query_start = start.astimezone(timezone.utc)
        spans = build_online_spans(
            events,
            covered or [TimeWindow(start.astimezone(zone), end.astimezone(zone))],
            start,
            end,
            is_self=is_self,
        )
        result = [
            {
                "start_minute": round((span.start - query_start).total_seconds() / 60),
                "end_minute": round((span.end - query_start).total_seconds() / 60),
                "status": span.status,
            }
            for span in spans
        ]
        return [span for span in result if span["end_minute"] > span["start_minute"]]

    @classmethod
    def _online_seconds(
        cls,
        events: list[dict[str, Any]],
        start: datetime,
        end: datetime,
        is_self: bool,
        covered: list[TimeWindow] | None = None,
    ) -> float:
        if end <= start:
            return 0.0
        spans = build_online_spans(
            events,
            covered or [TimeWindow(start, end)],
            start,
            end,
            is_self=is_self,
        )
        return sum((span.end - span.start).total_seconds() for span in spans)

    @classmethod
    def _world_spans(
        cls,
        events: list[dict[str, Any]],
        start: datetime,
        end: datetime,
        zone: ZoneInfo,
        is_self: bool = False,
        covered: list[TimeWindow] | None = None,
    ) -> list[dict[str, Any]]:
        if end <= start:
            return []
        query_start = start.astimezone(timezone.utc)
        spans = build_world_spans(
            events,
            covered or [TimeWindow(start.astimezone(zone), end.astimezone(zone))],
            start,
            end,
            is_self=is_self,
        )
        result = [
            {
                "start_minute": round((span.start - query_start).total_seconds() / 60),
                "end_minute": round((span.end - query_start).total_seconds() / 60),
                "status": span.status,
                "location": span.location,
                "world_id": span.world_id,
                "platform": span.platform,
                "location_kind": span.location_kind,
            }
            for span in spans
        ]
        return [span for span in result if span["end_minute"] > span["start_minute"]]

    def _snapshot(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        *,
        avatar: bool = False,
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        dict[str, list[dict[str, Any]]],
        tuple[Any, ...],
    ]:
        start_key = start.astimezone(timezone.utc).isoformat(timespec="microseconds")
        end_key = end.astimezone(timezone.utc).isoformat(timespec="microseconds")
        avatar_column = ",avatar_url" if avatar else ""
        sample_start_key = (
            start.astimezone(timezone.utc) - timedelta(seconds=7260)
        ).isoformat(timespec="microseconds")
        event_columns = (
            "client_event_id,friend_id,occurred_at,old_status,new_status,"
            "location,platform,source,anomaly,anomaly_reason"
        )
        event_select = (
            "e.client_event_id,e.friend_id,e.occurred_at,e.old_status,e.new_status,"
            "e.location,e.platform,e.source,"
            "CASE WHEN EXISTS(SELECT 1 FROM event_anomalies a "
            "WHERE a.tenant_id=e.tenant_id AND a.event_kind='status_event' "
            "AND a.event_id=e.client_event_id) THEN 1 ELSE 0 END AS anomaly,"
            "COALESCE((SELECT a.reason FROM event_anomalies a "
            "WHERE a.tenant_id=e.tenant_id AND a.event_kind='status_event' "
            "AND a.event_id=e.client_event_id ORDER BY a.detected_at LIMIT 1),'') "
            "AS anomaly_reason"
        )
        with self.store.lock, self.store.connection() as db:
            self.store._require_tenant(db, tenant_id)
            revision_row = db.execute(
                """SELECT COALESCE(MAX(rowid),0) AS event_revision,
                COALESCE((SELECT MAX(updated_at) FROM friends WHERE tenant_id=?),'') AS friend_revision,
                COALESCE((SELECT MAX(rowid) FROM collection_samples WHERE tenant_id=?),0) AS sample_revision,
                COALESCE((SELECT MAX(rowid) FROM friend_tracking_events WHERE tenant_id=?),0) AS tracking_revision,
                COALESCE((SELECT MAX(rowid) FROM event_anomalies WHERE tenant_id=?),0) AS anomaly_revision
                FROM status_events WHERE tenant_id=?""",
                (tenant_id, tenant_id, tenant_id, tenant_id, tenant_id),
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
                        SELECT {event_select},ROW_NUMBER() OVER (
                            PARTITION BY friend_id
                            ORDER BY occurred_at DESC,client_event_id DESC
                        ) AS rank
                        FROM status_events e
                        WHERE e.tenant_id=? AND e.occurred_at<?
                    ),windowed AS (
                        SELECT {event_select}
                        FROM status_events e
                        WHERE e.tenant_id=? AND e.occurred_at>=? AND e.occurred_at<?
                    )
                    SELECT {event_columns} FROM windowed
                    UNION ALL
                    SELECT {event_columns} FROM prior WHERE rank=1
                    ORDER BY friend_id,occurred_at,client_event_id""",
                    (tenant_id, start_key, tenant_id, start_key, end_key),
                ).fetchall()
            ]
            samples = [
                dict(row)
                for row in db.execute(
                    """SELECT sample_id,observed_at,source,outcome,authoritative,
                    expected_interval_seconds,friend_count,online_count,duration_ms,
                    error_category FROM collection_samples
                    WHERE tenant_id=? AND observed_at>=? AND observed_at<=?
                    ORDER BY observed_at,sample_id""",
                    (tenant_id, sample_start_key, end_key),
                ).fetchall()
            ]
            tracking = [
                dict(row)
                for row in db.execute(
                    """WITH prior AS (
                        SELECT event_id,friend_id,tracked,occurred_at,source,
                        ROW_NUMBER() OVER (
                            PARTITION BY friend_id
                            ORDER BY occurred_at DESC,event_id DESC
                        ) AS rank
                        FROM friend_tracking_events
                        WHERE tenant_id=? AND occurred_at<?
                    ),windowed AS (
                        SELECT event_id,friend_id,tracked,occurred_at,source
                        FROM friend_tracking_events
                        WHERE tenant_id=? AND occurred_at>=? AND occurred_at<?
                    )
                    SELECT event_id,friend_id,tracked,occurred_at,source FROM windowed
                    UNION ALL
                    SELECT event_id,friend_id,tracked,occurred_at,source
                    FROM prior WHERE rank=1
                    ORDER BY friend_id,occurred_at,event_id""",
                    (tenant_id, start_key, tenant_id, start_key, end_key),
                ).fetchall()
            ]
        revision = (
            int(revision_row["event_revision"] or 0),
            str(revision_row["friend_revision"] or ""),
            int(revision_row["sample_revision"] or 0),
            int(revision_row["tracking_revision"] or 0),
            int(revision_row["anomaly_revision"] or 0),
        )
        return friends, events, {"samples": samples, "tracking": tracking}, revision

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
        friends, events, evidence, _ = self._snapshot(tenant_id, start, end)
        grouped = self._group_events(events)
        tracking_grouped = self._group_events(evidence["tracking"])
        covered = build_observed_windows(
            evidence["samples"], range_start=start, range_end=end
        )
        totals: list[dict[str, Any]] = []
        online_now = 0
        for friend in friends:
            is_self = bool(friend["is_self"])
            if self._effective_state(friend["status"], friend["location"], is_self) != "offline":
                online_now += 1
            tracked = build_tracking_windows(
                tracking_grouped.get(str(friend["id"]), []),
                range_start=start,
                range_end=end,
            )
            person_coverage = intersect_windows(covered, tracked)
            seconds = self._online_seconds(
                grouped.get(str(friend["id"]), []),
                start,
                end,
                is_self,
                person_coverage,
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
        friends, events, evidence, revision = self._snapshot(
            tenant_id, query_start, query_end
        )
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
        tracking_grouped = self._group_events(evidence["tracking"])
        day_start, day_end = self._day_bounds(selected, self.zone, current)
        range_query_start = datetime.combine(
            range_start, time_type.min, tzinfo=self.zone
        )
        range_query_end = self._day_bounds(range_end, self.zone, current)[1]
        all_coverage = build_observed_windows(
            evidence["samples"], range_start=query_start, range_end=query_end
        )
        selected_coverage = intersect_windows(
            all_coverage, [TimeWindow(day_start, day_end)]
        )
        range_coverage = intersect_windows(
            all_coverage, [TimeWindow(range_query_start, range_query_end)]
        )
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
            for hour in range(24):
                hour_window = self._hour_window(
                    observed_day, hour, self.zone, current
                )
                if hour_window is not None:
                    observed_minutes[hour] += sum(
                        window.minutes
                        for window in intersect_windows(
                            range_coverage, [hour_window]
                        )
                    )
        for friend in friends:
            friend_id = str(friend["id"])
            friend_events = grouped.get(friend_id, [])
            friend_tracking = tracking_grouped.get(friend_id, [])
            is_self = bool(friend["is_self"])
            selected_tracking = build_tracking_windows(
                friend_tracking, range_start=day_start, range_end=day_end
            )
            selected_person_coverage = intersect_windows(
                selected_coverage, selected_tracking
            )
            selected_spans = self._online_spans(
                friend_events,
                day_start,
                day_end,
                self.zone,
                is_self,
                selected_person_coverage,
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
            range_tracking = build_tracking_windows(
                friend_tracking,
                range_start=range_query_start,
                range_end=range_query_end,
            )
            range_online = [
                span.window
                for span in build_online_spans(
                    friend_events,
                    range_coverage,
                    range_query_start,
                    range_query_end,
                    is_self=is_self,
                )
            ]
            online_by_hour = [0.0] * 24
            observed_by_hour = [0.0] * 24
            eligible_by_hour = [0.0] * 24
            covered_days_by_hour = [0] * 24
            for offset in range(range_days):
                current_day = range_start + timedelta(days=offset)
                for hour in range(24):
                    hour_window = self._hour_window(
                        current_day, hour, self.zone, current
                    )
                    if hour_window is None:
                        continue
                    cell = activity_cell(
                        online=range_online,
                        covered=range_coverage,
                        tracked=range_tracking,
                        hour_start=hour_window.start,
                        hour_end=hour_window.end,
                    )
                    online_by_hour[hour] += cell.online_minutes
                    observed_by_hour[hour] += cell.observed_minutes
                    eligible_by_hour[hour] += cell.eligible_minutes
                    if cell.observed_minutes > 0:
                        covered_days_by_hour[hour] += 1
            cells: list[dict[str, Any]] = []
            for hour in range(24):
                minimum_evidence = max(30.0, eligible_by_hour[hour] * 0.1)
                ratio = (
                    min(1.0, online_by_hour[hour] / observed_by_hour[hour])
                    if observed_by_hour[hour] >= minimum_evidence
                    and observed_by_hour[hour] > 0
                    else None
                )
                cells.append(
                    {
                        "ratio": round(ratio, 3) if ratio is not None else None,
                        "online_minutes": round(online_by_hour[hour], 1),
                        "observed_minutes": round(observed_by_hour[hour], 1),
                        "eligible_minutes": round(eligible_by_hour[hour], 1),
                        "covered_days": covered_days_by_hour[hour],
                        "range_days": range_days,
                    }
                )
            heatmap.append(
                {
                    "id": friend_id,
                    "name": str(friend["display_name"]),
                    "is_self": is_self,
                    "tracking_started_at": next(
                        (
                            str(item["occurred_at"])
                            for item in friend_tracking
                            if bool(item["tracked"])
                        ),
                        None,
                    ),
                    "cells": cells,
                    "values": [
                        cell["ratio"] if cell["ratio"] is not None else 0.0
                        for cell in cells
                    ],
                }
            )

        selected_coverage_payload = self._coverage_payload(
            selected_coverage, day_start, day_end
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
            "coverage": selected_coverage_payload,
            "heatmap_coverage": self._coverage_payload(
                range_coverage, range_query_start, range_query_end
            ),
            "gaps": selected_coverage_payload["gaps"],
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
        friends, events, evidence, revision = self._snapshot(
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
        tracking_grouped = self._group_events(evidence["tracking"])
        covered = build_observed_windows(
            evidence["samples"], range_start=day_start, range_end=day_end
        )
        rows: list[dict[str, Any]] = []
        world_ids: set[str] = set()
        for friend in friends:
            friend_id = str(friend["id"])
            tracked = build_tracking_windows(
                tracking_grouped.get(friend_id, []),
                range_start=day_start,
                range_end=day_end,
            )
            person_coverage = intersect_windows(covered, tracked)
            spans = self._world_spans(
                grouped.get(friend_id, []),
                day_start,
                day_end,
                self.zone,
                bool(friend["is_self"]),
                person_coverage,
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
        coverage_payload = self._coverage_payload(covered, day_start, day_end)
        result = {
            "day": selected.isoformat(),
            "future_clamped": future_clamped,
            "timezone": self.zone.key,
            "self_id": next((row["id"] for row in rows if row["is_self"]), ""),
            "friends": rows,
            "world_ids": sorted(world_ids),
            "coverage": coverage_payload,
            "gaps": coverage_payload["gaps"],
        }
        with self._cache_lock:
            if len(self._world_cache) > 128:
                self._world_cache.clear()
            self._world_cache[cache_key] = (time.monotonic(), result)
        return copy.deepcopy(result)

    def coverage_overview(
        self,
        tenant_id: str,
        range_from: str | None,
        range_to: str | None,
    ) -> dict[str, Any]:
        current = datetime.now(self.zone)
        today = current.date()
        end_day = min(_parse_date(range_to, "结束日期") or today, today)
        start_day = _parse_date(range_from, "起始日期") or end_day
        if start_day > end_day:
            raise ValueError("起始日期不能晚于结束日期")
        range_days = (end_day - start_day).days + 1
        if range_days > 730:
            raise ValueError("范围不能超过 730 天")
        start = datetime.combine(start_day, time_type.min, tzinfo=self.zone)
        end = self._day_bounds(end_day, self.zone, current)[1]
        _, _, evidence, _ = self._snapshot(tenant_id, start, end)
        covered = build_observed_windows(
            evidence["samples"], range_start=start, range_end=end
        )
        payload = self._coverage_payload(covered, start, end)
        return {
            "from": start_day.isoformat(),
            "to": end_day.isoformat(),
            "range_days": range_days,
            "timezone": self.zone.key,
            **payload,
        }


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
