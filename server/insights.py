from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from vrchat_monitor.vrchat import world_id_from_location

from .analytics import AnalyticsService
from .observation import (
    TimeWindow,
    activity_cell,
    build_observed_windows,
    build_online_spans,
    build_tracking_windows,
    build_world_spans,
    coverage_summary,
    intersect_windows,
)
from .storage import Store


def canonical_instance(location: str) -> str | None:
    normalized = str(location or "").strip()
    if world_id_from_location(normalized) is None or ":" not in normalized:
        return None
    world_id, instance = normalized.split(":", 1)
    if not instance.strip():
        return None
    return f"{world_id.lower()}:{instance.strip().lower()}"


def same_instance(location_a: str, location_b: str) -> bool:
    first = canonical_instance(location_a)
    second = canonical_instance(location_b)
    return first is not None and first == second


def _parse_date(value: str, label: str) -> date:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError as error:
        raise ValueError(f"{label}必须是 YYYY-MM-DD") from error


class InsightsService:
    def __init__(self, store: Store):
        self.store = store

    @staticmethod
    def _events(
        db: Any,
        tenant_id: str,
        friend_id: str,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        start_key = start.astimezone(timezone.utc).isoformat(timespec="microseconds")
        end_key = end.astimezone(timezone.utc).isoformat(timespec="microseconds")
        rows = db.execute(
            """WITH prior AS (
                SELECT e.client_event_id,e.friend_id,e.occurred_at,e.old_status,
                e.new_status,e.location,e.platform,e.source,
                CASE WHEN EXISTS(
                    SELECT 1 FROM event_anomalies a
                    WHERE a.tenant_id=e.tenant_id AND a.event_kind='status_event'
                      AND a.event_id=e.client_event_id
                ) THEN 1 ELSE 0 END AS anomaly,
                ROW_NUMBER() OVER (
                    ORDER BY e.occurred_at DESC,e.client_event_id DESC
                ) AS rank
                FROM status_events e
                WHERE e.tenant_id=? AND e.friend_id=? AND e.occurred_at<?
            ),windowed AS (
                SELECT e.client_event_id,e.friend_id,e.occurred_at,e.old_status,
                e.new_status,e.location,e.platform,e.source,
                CASE WHEN EXISTS(
                    SELECT 1 FROM event_anomalies a
                    WHERE a.tenant_id=e.tenant_id AND a.event_kind='status_event'
                      AND a.event_id=e.client_event_id
                ) THEN 1 ELSE 0 END AS anomaly
                FROM status_events e
                WHERE e.tenant_id=? AND e.friend_id=?
                  AND e.occurred_at>=? AND e.occurred_at<?
            )
            SELECT client_event_id,friend_id,occurred_at,old_status,new_status,
            location,platform,source,anomaly FROM windowed
            UNION ALL
            SELECT client_event_id,friend_id,occurred_at,old_status,new_status,
            location,platform,source,anomaly FROM prior WHERE rank=1
            ORDER BY occurred_at,client_event_id""",
            (
                tenant_id,
                friend_id,
                start_key,
                tenant_id,
                friend_id,
                start_key,
                end_key,
            ),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _tracking(
        db: Any,
        tenant_id: str,
        friend_id: str,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        start_key = start.astimezone(timezone.utc).isoformat(timespec="microseconds")
        end_key = end.astimezone(timezone.utc).isoformat(timespec="microseconds")
        rows = db.execute(
            """WITH prior AS (
                SELECT event_id,friend_id,tracked,occurred_at,source,
                ROW_NUMBER() OVER (
                    ORDER BY occurred_at DESC,event_id DESC
                ) AS rank
                FROM friend_tracking_events
                WHERE tenant_id=? AND friend_id=? AND occurred_at<?
            ),windowed AS (
                SELECT event_id,friend_id,tracked,occurred_at,source
                FROM friend_tracking_events
                WHERE tenant_id=? AND friend_id=?
                  AND occurred_at>=? AND occurred_at<?
            )
            SELECT event_id,friend_id,tracked,occurred_at,source FROM windowed
            UNION ALL
            SELECT event_id,friend_id,tracked,occurred_at,source
            FROM prior WHERE rank=1
            ORDER BY occurred_at,event_id""",
            (
                tenant_id,
                friend_id,
                start_key,
                tenant_id,
                friend_id,
                start_key,
                end_key,
            ),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _minutes(windows: list[TimeWindow]) -> float:
        return sum(window.minutes for window in windows)

    @staticmethod
    def _co_presence(first: list[Any], second: list[Any]) -> float:
        total = 0.0
        left = 0
        right = 0
        while left < len(first) and right < len(second):
            a = first[left]
            b = second[right]
            if same_instance(a.location, b.location):
                overlap = a.window.intersection(b.window)
                if overlap is not None:
                    total += overlap.minutes
            if a.end <= b.end:
                left += 1
            else:
                right += 1
        return total

    @staticmethod
    def _coverage_payload(covered: list[TimeWindow], start: datetime, end: datetime) -> dict[str, Any]:
        summary = coverage_summary(covered, range_start=start, range_end=end)
        return {
            "expected_minutes": round(summary.expected_minutes, 1),
            "observed_minutes": round(summary.observed_minutes, 1),
            "ratio": round(summary.ratio, 4),
            "first_observed": summary.first_observed.isoformat() if summary.first_observed else None,
            "last_observed": summary.last_observed.isoformat() if summary.last_observed else None,
            "gaps": [
                {
                    "start": gap.start.isoformat(),
                    "end": gap.end.isoformat(),
                    "minutes": round(gap.minutes, 1),
                }
                for gap in summary.gaps
            ],
        }

    def friend(
        self,
        tenant_id: str,
        friend_id: str,
        range_from: str,
        range_to: str,
    ) -> dict[str, Any]:
        with self.store.lock, self.store.connection() as db:
            preference = db.execute(
                "SELECT timezone FROM tenant_preferences WHERE tenant_id=?",
                (tenant_id,),
            ).fetchone()
            zone = ZoneInfo(str(preference["timezone"]) if preference else "Asia/Shanghai")
            current = datetime.now(zone)
            start_day = _parse_date(range_from, "起始日期")
            end_day = min(_parse_date(range_to, "结束日期"), current.date())
            if start_day > end_day:
                raise ValueError("起始日期不能晚于结束日期")
            if (end_day - start_day).days + 1 > 366:
                raise ValueError("范围不能超过 366 天")
            start = datetime.combine(start_day, time.min, tzinfo=zone)
            end = datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=zone)
            if end_day == current.date():
                end = min(end, current)

            friend = db.execute(
                """SELECT id,username,display_name,is_self,status,status_description,
                location,platform,avatar_url,avatar_image_url,bio,bio_links,last_seen,
                last_changed,updated_at FROM friends WHERE tenant_id=? AND id=?""",
                (tenant_id, friend_id),
            ).fetchone()
            if friend is None:
                raise KeyError("friend not found")
            self_row = db.execute(
                """SELECT id FROM friends WHERE tenant_id=? AND is_self=1
                ORDER BY updated_at DESC,id LIMIT 1""",
                (tenant_id,),
            ).fetchone()
            self_id = str(self_row["id"]) if self_row else ""

            sample_start = (
                start.astimezone(timezone.utc) - timedelta(seconds=7260)
            ).isoformat(timespec="microseconds")
            samples = [
                dict(row)
                for row in db.execute(
                    """SELECT observed_at,outcome,authoritative,
                    expected_interval_seconds FROM collection_samples
                    WHERE tenant_id=? AND observed_at>=? AND observed_at<=?
                    ORDER BY observed_at,sample_id""",
                    (
                        tenant_id,
                        sample_start,
                        end.astimezone(timezone.utc).isoformat(timespec="microseconds"),
                    ),
                ).fetchall()
            ]
            friend_events = self._events(db, tenant_id, friend_id, start, end)
            friend_tracking_events = self._tracking(
                db, tenant_id, friend_id, start, end
            )
            self_events = (
                self._events(db, tenant_id, self_id, start, end) if self_id else []
            )
            self_tracking_events = (
                self._tracking(db, tenant_id, self_id, start, end)
                if self_id
                else []
            )
            identity_events = [
                dict(row)
                for row in db.execute(
                    """SELECT event_id,field,old_value,new_value,occurred_at,source
                    FROM friend_identity_events WHERE tenant_id=? AND friend_id=?
                    ORDER BY occurred_at DESC,event_id DESC LIMIT 500""",
                    (tenant_id, friend_id),
                ).fetchall()
            ]
            first_row = db.execute(
                """SELECT MIN(value) AS first_recorded FROM (
                    SELECT MIN(occurred_at) AS value FROM status_events
                    WHERE tenant_id=? AND friend_id=?
                    UNION ALL
                    SELECT MIN(occurred_at) AS value FROM friend_tracking_events
                    WHERE tenant_id=? AND friend_id=?
                ) WHERE value IS NOT NULL""",
                (tenant_id, friend_id, tenant_id, friend_id),
            ).fetchone()

        covered = build_observed_windows(samples, range_start=start, range_end=end)
        friend_tracking = build_tracking_windows(
            friend_tracking_events, range_start=start, range_end=end
        )
        friend_coverage = intersect_windows(covered, friend_tracking)
        friend_online_spans = build_online_spans(
            friend_events,
            friend_coverage,
            start,
            end,
            is_self=bool(friend["is_self"]),
        )
        friend_online = [span.window for span in friend_online_spans]
        friend_worlds = build_world_spans(
            friend_events,
            friend_coverage,
            start,
            end,
            is_self=bool(friend["is_self"]),
        )

        self_tracking = build_tracking_windows(
            self_tracking_events, range_start=start, range_end=end
        )
        self_coverage = intersect_windows(covered, self_tracking)
        self_online_spans = build_online_spans(
            self_events, self_coverage, start, end, is_self=True
        )
        self_online = [span.window for span in self_online_spans]
        self_worlds = build_world_spans(
            self_events, self_coverage, start, end, is_self=True
        )
        overlap = intersect_windows(friend_online, self_online)

        world_totals: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"minutes": 0.0, "visits": 0, "last_observed": None}
        )
        for span in friend_worlds:
            if not span.world_id:
                continue
            item = world_totals[span.world_id]
            item["minutes"] += span.window.minutes
            item["visits"] += 1
            if item["last_observed"] is None or span.end > item["last_observed"]:
                item["last_observed"] = span.end
        most_worlds: list[dict[str, Any]] = []
        with self.store.lock, self.store.connection() as db:
            for world_id, values in world_totals.items():
                cached = db.execute(
                    "SELECT payload_json FROM world_cache WHERE world_id=?", (world_id,)
                ).fetchone()
                try:
                    payload = json.loads(str(cached["payload_json"])) if cached else {}
                except json.JSONDecodeError:
                    payload = {}
                most_worlds.append(
                    {
                        "world_id": world_id,
                        "name": str(payload.get("name") or world_id),
                        "minutes": round(float(values["minutes"]), 1),
                        "visits": int(values["visits"]),
                        "last_observed": values["last_observed"].isoformat(),
                    }
                )
        most_worlds.sort(
            key=lambda item: (-item["minutes"], -item["visits"], item["world_id"])
        )

        hourly: list[dict[str, Any]] = []
        for hour in range(24):
            online_minutes = observed_minutes = eligible_minutes = 0.0
            covered_days = 0
            for offset in range((end_day - start_day).days + 1):
                day = start_day + timedelta(days=offset)
                hour_window = AnalyticsService._hour_window(day, hour, zone, current)
                if hour_window is None:
                    continue
                cell = activity_cell(
                    online=friend_online,
                    covered=covered,
                    tracked=friend_tracking,
                    hour_start=hour_window.start,
                    hour_end=hour_window.end,
                )
                online_minutes += cell.online_minutes
                observed_minutes += cell.observed_minutes
                eligible_minutes += cell.eligible_minutes
                covered_days += int(cell.observed_minutes > 0)
            minimum = max(30.0, eligible_minutes * 0.1)
            ratio = (
                online_minutes / observed_minutes
                if observed_minutes >= minimum and observed_minutes > 0
                else None
            )
            hourly.append(
                {
                    "hour": hour,
                    "ratio": round(min(1.0, ratio), 3) if ratio is not None else None,
                    "online_minutes": round(online_minutes, 1),
                    "observed_minutes": round(observed_minutes, 1),
                    "eligible_minutes": round(eligible_minutes, 1),
                    "covered_days": covered_days,
                    "range_days": (end_day - start_day).days + 1,
                }
            )

        profile = dict(friend)
        try:
            profile["bio_links"] = json.loads(str(profile.get("bio_links") or "[]"))
        except json.JSONDecodeError:
            profile["bio_links"] = []
        return {
            "friend": profile,
            "from": start_day.isoformat(),
            "to": end_day.isoformat(),
            "timezone": zone.key,
            "first_recorded_at": str(first_row["first_recorded"] or "") or None,
            "latest_observed_online": max(
                (span.end for span in friend_online_spans), default=None
            ).isoformat()
            if friend_online_spans
            else None,
            "online_minutes": round(self._minutes(friend_online), 1),
            "online_overlap_minutes": round(self._minutes(overlap), 1),
            "co_presence_minutes": round(
                self._co_presence(friend_worlds, self_worlds), 1
            ),
            "most_visited_worlds": most_worlds,
            "hourly_activity": hourly,
            "identity_events": identity_events,
            "coverage": self._coverage_payload(friend_coverage, start, end),
            "gaps": self._coverage_payload(friend_coverage, start, end)["gaps"],
        }
