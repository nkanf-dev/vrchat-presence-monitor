from __future__ import annotations

import base64
import copy
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from vrchat_monitor.vrchat import VRChatError, world_id_from_location

from .observation import (
    TimeWindow,
    build_observed_windows,
    build_tracking_windows,
    build_world_spans,
    coverage_summary,
    intersect_windows,
)
from .storage import Store, now


WORLD_CACHE_SECONDS = 24 * 60 * 60
WorldFetcher = Callable[[str, str], dict[str, Any]]
Clock = Callable[[], datetime]


@dataclass(slots=True)
class ResolveRequest:
    world_id: str
    tenant_ids: list[str]
    inflight: bool = False


def require_world_id(value: str) -> str:
    normalized = world_id_from_location(str(value or ""))
    if not normalized:
        raise ValueError("世界 ID 格式无效")
    return normalized.lower()


def normalize_world(world_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": world_id,
        "name": str(raw.get("name") or world_id),
        "description": str(raw.get("description") or ""),
        "thumbnail_url": str(
            raw.get("thumbnail_url")
            or raw.get("thumbnailImageUrl")
            or raw.get("imageUrl")
            or ""
        ),
        "image_url": str(raw.get("image_url") or raw.get("imageUrl") or ""),
        "author_id": str(raw.get("author_id") or raw.get("authorId") or ""),
        "author_name": str(raw.get("author_name") or raw.get("authorName") or ""),
        "capacity": raw.get("capacity"),
        "recommended_capacity": raw.get("recommended_capacity")
        if "recommended_capacity" in raw
        else raw.get("recommendedCapacity"),
        "occupants": raw.get("occupants"),
        "visits": raw.get("visits"),
        "favorites": raw.get("favorites"),
        "popularity": raw.get("popularity"),
        "heat": raw.get("heat"),
        "release_status": str(
            raw.get("release_status") or raw.get("releaseStatus") or ""
        ),
        "organization": str(raw.get("organization") or ""),
        "tags": raw.get("tags") if isinstance(raw.get("tags"), list) else [],
        "publication_date": str(
            raw.get("publication_date") or raw.get("publicationDate") or ""
        ),
        "created_at": str(raw.get("created_at") or raw.get("createdAt") or ""),
        "updated_at": str(raw.get("updated_at") or raw.get("updatedAt") or ""),
    }


def world_metadata_tags(raw: dict[str, Any] | None) -> tuple[str, ...]:
    """Return stable, unique VRChat metadata tags from a cached world payload."""
    if not isinstance(raw, dict) or not isinstance(raw.get("tags"), list):
        return ()
    unique: dict[str, str] = {}
    for value in raw["tags"]:
        if not isinstance(value, str):
            continue
        name = value.strip()
        if name:
            unique.setdefault(name.casefold(), name)
    return tuple(sorted(unique.values(), key=str.casefold))


def world_has_metadata_tag(raw: dict[str, Any] | None, selected: str) -> bool:
    folded = str(selected or "").strip().casefold()
    return not folded or any(
        name.casefold() == folded for name in world_metadata_tags(raw)
    )


class WorldResolver:
    def __init__(
        self,
        store: Store,
        fetcher: WorldFetcher,
        *,
        max_backoff_seconds: int = 1800,
        min_request_interval_seconds: float = 1.0,
        clock: Clock | None = None,
        monotonic: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ):
        self.store = store
        self.fetcher = fetcher
        self.max_backoff_seconds = max(60, int(max_backoff_seconds))
        self.min_request_interval_seconds = max(
            0.0, float(min_request_interval_seconds)
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.monotonic
        self._sleeper = sleeper or time.sleep
        self.lock = threading.RLock()
        self._drain_lock = threading.Lock()
        self._pending: dict[str, ResolveRequest] = {}
        self._last_request_started_at: float | None = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name="world-resolver", daemon=True
        )

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    def _state(self, world_id: str) -> dict[str, Any] | None:
        with self.store.lock, self.store.connection() as db:
            row = db.execute(
                """SELECT world_id,outcome,attempts,retry_at,error_category,updated_at
                FROM world_resolution_state WHERE world_id=?""",
                (world_id,),
            ).fetchone()
            return dict(row) if row else None

    def _retry_pending(self, state: dict[str, Any] | None) -> bool:
        if not state or not state.get("retry_at"):
            return False
        try:
            retry_at = datetime.fromisoformat(
                str(state["retry_at"]).replace("Z", "+00:00")
            )
        except ValueError:
            return False
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        current = self._clock()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return retry_at.astimezone(timezone.utc) > current.astimezone(timezone.utc)

    def enqueue(self, tenant_id: str, world_id: str) -> bool:
        normalized = require_world_id(world_id)
        if self.store.world_cache_get(
            normalized, max_age_seconds=WORLD_CACHE_SECONDS
        ) is not None:
            return False
        state = self._state(normalized)
        if self._retry_pending(state):
            return False
        with self.lock:
            request = self._pending.get(normalized)
            added_world = request is None
            if request is None:
                request = ResolveRequest(normalized, [])
                self._pending[normalized] = request
            if tenant_id not in request.tenant_ids:
                request.tenant_ids.append(tenant_id)
            self._wake.set()
            return added_world

    def is_pending(self, world_id: str) -> bool:
        normalized = require_world_id(world_id)
        with self.lock:
            return normalized in self._pending

    def _persist_state(
        self,
        world_id: str,
        outcome: str,
        attempts: int,
        retry_at: str | None,
        error_category: str,
    ) -> None:
        with self.store.lock, self.store.connection() as db:
            db.execute(
                """INSERT INTO world_resolution_state(
                    world_id,outcome,attempts,retry_at,error_category,updated_at
                ) VALUES(?,?,?,?,?,?) ON CONFLICT(world_id) DO UPDATE SET
                    outcome=excluded.outcome,attempts=excluded.attempts,
                    retry_at=excluded.retry_at,error_category=excluded.error_category,
                    updated_at=excluded.updated_at""",
                (world_id, outcome, attempts, retry_at, error_category, now()),
            )

    def _next_request(self) -> ResolveRequest | None:
        with self.lock:
            for request in self._pending.values():
                if not request.inflight:
                    request.inflight = True
                    return request
        return None

    def _finish_request(self, request: ResolveRequest) -> None:
        with self.lock:
            if self._pending.get(request.world_id) is request:
                self._pending.pop(request.world_id, None)

    def _next_tenant(
        self, request: ResolveRequest, index: int
    ) -> tuple[str | None, int]:
        with self.lock:
            current = self._pending.get(request.world_id)
            if current is not request or index >= len(request.tenant_ids):
                return None, index
            return request.tenant_ids[index], index + 1

    def _pace_request(self) -> None:
        current = self._monotonic()
        if self._last_request_started_at is not None:
            remaining = self.min_request_interval_seconds - (
                current - self._last_request_started_at
            )
            if remaining > 0:
                self._sleeper(remaining)
                current = self._monotonic()
        self._last_request_started_at = current

    def drain_once(self) -> bool:
        with self._drain_lock:
            request = self._next_request()
            if request is None:
                return False
            world_id = request.world_id
            if self.store.world_cache_get(
                world_id, max_age_seconds=WORLD_CACHE_SECONDS
            ) is not None:
                self._finish_request(request)
                return True
            state = self._state(world_id)
            if self._retry_pending(state):
                self._finish_request(request)
                return False

            attempts = int((state or {}).get("attempts") or 0) + 1
            last_error: Exception | None = None
            tenant_index = 0
            while True:
                tenant_id, tenant_index = self._next_tenant(request, tenant_index)
                if tenant_id is None:
                    break
                try:
                    self._pace_request()
                    raw = self.fetcher(tenant_id, world_id)
                    payload = normalize_world(world_id, raw)
                    self.store.world_cache_put(world_id, payload)
                    self._persist_state(world_id, "ready", 0, None, "")
                    self._finish_request(request)
                    return True
                except Exception as error:  # Persist only a stable category.
                    last_error = error
                    if isinstance(error, VRChatError) and error.status == 401:
                        continue
                    break

            self._finish_request(request)
            retry_after = (
                max(0.0, float(last_error.retry_after or 0))
                if isinstance(last_error, VRChatError)
                else 0.0
            )
            exponential = min(
                float(self.max_backoff_seconds),
                max(60.0, float(2 ** min(attempts, 10) * 15)),
            )
            delay = max(retry_after, exponential)
            current = self._clock()
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            retry_at = (
                current.astimezone(timezone.utc) + timedelta(seconds=delay)
            ).isoformat(timespec="microseconds")
            category = (
                "rate_limited"
                if isinstance(last_error, VRChatError) and last_error.status == 429
                else "not_found"
                if isinstance(last_error, VRChatError) and last_error.status == 404
                else "session_expired"
                if isinstance(last_error, VRChatError) and last_error.status == 401
                else "upstream"
            )
            outcome = "unavailable" if category == "not_found" else "retry"
            self._persist_state(world_id, outcome, attempts, retry_at, category)
            return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            if not self.drain_once():
                self._wake.wait(1)
                self._wake.clear()


def _require_filter_ids(
    db: Any,
    tenant_id: str,
    *,
    friend_id: str = "",
) -> None:
    if friend_id and db.execute(
        "SELECT 1 FROM friends WHERE tenant_id=? AND id=?",
        (tenant_id, friend_id),
    ).fetchone() is None:
        raise KeyError("friend not found")
def _public_resolution_status(state: dict[str, Any] | None) -> str:
    if state and (
        str(state.get("outcome") or "") == "unavailable"
        or str(state.get("error_category") or "") == "not_found"
    ):
        return "unavailable"
    return "pending"


class WorldService:
    def __init__(self, store: Store, resolver: WorldResolver):
        self.store = store
        self.resolver = resolver

    @staticmethod
    def _world_expression(column: str = "location") -> str:
        return (
            f"CASE WHEN instr({column},':')>0 "
            f"THEN substr({column},1,instr({column},':')-1) ELSE {column} END"
        )

    def _is_observed(self, tenant_id: str, world_id: str) -> bool:
        expression = self._world_expression()
        current_expression = self._world_expression("location")
        with self.store.lock, self.store.connection() as db:
            self.store._require_tenant(db, tenant_id)
            return db.execute(
                f"""SELECT 1 FROM (
                    SELECT 1 FROM friends WHERE tenant_id=?
                      AND location GLOB 'wrld_*' AND lower({current_expression})=?
                    UNION ALL
                    SELECT 1 FROM status_events WHERE tenant_id=?
                      AND location GLOB 'wrld_*' AND lower({expression})=?
                      AND NOT EXISTS(SELECT 1 FROM event_anomalies a
                          WHERE a.tenant_id=status_events.tenant_id
                            AND a.event_kind='status_event'
                            AND a.event_id=status_events.client_event_id)
                ) LIMIT 1""",
                (tenant_id, world_id, tenant_id, world_id),
            ).fetchone() is not None

    def _presentation(self, tenant_id: str, world_id: str) -> dict[str, Any]:
        cached = self.store.world_cache_get(world_id)
        fresh = self.store.world_cache_get(
            world_id, max_age_seconds=WORLD_CACHE_SECONDS
        )
        if cached is not None:
            stale = fresh is None
            if stale:
                self.resolver.enqueue(tenant_id, world_id)
            return {
                **normalize_world(world_id, cached),
                "resolution_status": "ready",
                "stale": stale,
            }
        self.resolver.enqueue(tenant_id, world_id)
        return {
            **normalize_world(world_id, {}),
            "resolution_status": "pending"
            if self.resolver.is_pending(world_id)
            else _public_resolution_status(self.resolver._state(world_id)),
            "stale": False,
        }

    def detail(self, tenant_id: str, world_id: str) -> dict[str, Any]:
        normalized = require_world_id(world_id)
        if not self._is_observed(tenant_id, normalized):
            raise KeyError("world not observed")
        return self._presentation(tenant_id, normalized)

    def _observed_world_rows(
        self,
        tenant_id: str,
        *,
        friend_id: str = "",
    ) -> list[dict[str, Any]]:
        expression = self._world_expression("e.location")
        clauses = [
            "e.tenant_id=?",
            "e.location GLOB 'wrld_*'",
            """NOT EXISTS(SELECT 1 FROM event_anomalies a
            WHERE a.tenant_id=e.tenant_id AND a.event_kind='status_event'
              AND a.event_id=e.client_event_id)""",
        ]
        params: list[Any] = [tenant_id]
        if friend_id:
            clauses.append("e.friend_id=?")
            params.append(friend_id)
        with self.store.lock, self.store.connection() as db:
            self.store._require_tenant(db, tenant_id)
            _require_filter_ids(db, tenant_id, friend_id=friend_id)
            return [
                dict(row)
                for row in db.execute(
                    f"""SELECT {expression} AS world_id,
                    MAX(e.occurred_at) AS last_observed,COUNT(*) AS event_count
                    FROM status_events e WHERE {' AND '.join(clauses)}
                    GROUP BY world_id ORDER BY last_observed DESC,world_id
                    LIMIT 5000""",
                    params,
                ).fetchall()
            ]

    def tags(
        self,
        tenant_id: str,
        *,
        friend_id: str = "",
    ) -> list[dict[str, Any]]:
        counts: dict[str, tuple[str, int]] = {}
        for row in self._observed_world_rows(tenant_id, friend_id=friend_id):
            try:
                world_id = require_world_id(str(row["world_id"]))
            except ValueError:
                continue
            for name in world_metadata_tags(self.store.world_cache_get(world_id)):
                folded = name.casefold()
                display_name, count = counts.get(folded, (name, 0))
                counts[folded] = (display_name, count + 1)
        return [
            {"name": name, "count": count}
            for name, count in sorted(
                counts.values(), key=lambda item: (-item[1], item[0].casefold())
            )
        ]

    def library(
        self,
        tenant_id: str,
        *,
        query: str = "",
        author: str = "",
        friend_id: str = "",
        world_tag: str = "",
        cursor: str = "",
        offset: int | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        page_size = max(1, min(int(limit), 100))
        if offset is None:
            try:
                page_offset = (
                    int(base64.urlsafe_b64decode(cursor + "===").decode())
                    if cursor
                    else 0
                )
            except (ValueError, UnicodeDecodeError):
                raise ValueError("分页位置无效") from None
        else:
            page_offset = int(offset)
        page_offset = max(0, page_offset)
        rows = self._observed_world_rows(tenant_id, friend_id=friend_id)

        items: list[dict[str, Any]] = []
        folded_query = str(query or "").strip().casefold()
        folded_author = str(author or "").strip().casefold()
        for row in rows:
            try:
                world_id = require_world_id(str(row["world_id"]))
            except ValueError:
                continue
            cached = self.store.world_cache_get(world_id)
            payload = cached or {}
            world_author = str(
                payload.get("author_name") or payload.get("authorName") or ""
            )
            searchable = " ".join(
                (
                    world_id,
                    str(payload.get("name") or ""),
                    world_author,
                    str(payload.get("description") or ""),
                )
            ).casefold()
            if folded_query and folded_query not in searchable:
                continue
            if folded_author and folded_author not in world_author.casefold():
                continue
            if not world_has_metadata_tag(payload, world_tag):
                continue
            items.append(
                {
                    "world_id": world_id,
                    "last_observed": str(row["last_observed"]),
                    "event_count": int(row["event_count"]),
                }
            )

        raw_page = items[page_offset : page_offset + page_size]
        page = [
            {
                **self._presentation(tenant_id, item["world_id"]),
                "last_observed": item["last_observed"],
                "event_count": item["event_count"],
            }
            for item in raw_page
        ]
        next_offset = page_offset + len(raw_page)
        next_cursor = (
            base64.urlsafe_b64encode(str(next_offset).encode()).decode().rstrip("=")
            if next_offset < len(items)
            else None
        )
        return {
            "items": page,
            "next_cursor": next_cursor,
            "total": len(items),
        }


class DiscoveryService:
    ALLOWED_DAYS = frozenset({0, 1, 7, 30})
    _CACHE_SECONDS = 60.0
    _CACHE_LIMIT = 128

    def __init__(
        self,
        store: Store,
        resolver: WorldResolver,
        *,
        clock: Clock | None = None,
    ):
        self.store = store
        self.resolver = resolver
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._cache_lock = threading.RLock()
        self._cache: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}

    @staticmethod
    def _event_select() -> str:
        return (
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

    @staticmethod
    def _group_by_friend(
        rows: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[str(row["friend_id"])].append(row)
        return grouped

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")

    def _current_time(self) -> datetime:
        current = self._clock()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc)

    def _scope(
        self,
        tenant_id: str,
        *,
        friend_id: str,
        friend_ids: list[str] | None = None,
        include_self: bool,
    ) -> dict[str, Any]:
        with self.store.lock, self.store.connection() as db:
            self.store._require_tenant(db, tenant_id)
            selected_ids = list(
                dict.fromkeys(
                    [friend_id]
                    if friend_id
                    else [str(value) for value in (friend_ids or []) if str(value)]
                )
            )
            if selected_ids:
                placeholders = ",".join("?" for _ in selected_ids)
                found = {
                    str(row["id"])
                    for row in db.execute(
                        f"SELECT id FROM friends WHERE tenant_id=? AND id IN ({placeholders})",
                        (tenant_id, *selected_ids),
                    ).fetchall()
                }
                if found != set(selected_ids):
                    raise KeyError("friend not found")
            friend_clauses = [
                "f.tenant_id=?",
                "substr(f.id,1,4) NOT IN ('not_','frq_')",
            ]
            friend_params: list[Any] = [tenant_id]
            if selected_ids:
                placeholders = ",".join("?" for _ in selected_ids)
                friend_clauses.append(f"f.id IN ({placeholders})")
                friend_params.extend(selected_ids)
            if not include_self:
                friend_clauses.append("f.is_self=0")
            friends = [
                dict(row)
                for row in db.execute(
                    f"""SELECT f.id,f.display_name,f.is_self FROM friends f
                    WHERE {' AND '.join(friend_clauses)} ORDER BY f.id""",
                    friend_params,
                ).fetchall()
            ]
            friend_ids = [str(row["id"]) for row in friends]
            revision = db.execute(
                """SELECT
                COALESCE((SELECT MAX(rowid) FROM status_events WHERE tenant_id=?),0)
                    AS event_revision,
                COALESCE((SELECT MAX(rowid) FROM collection_samples WHERE tenant_id=?),0)
                    AS sample_revision,
                COALESCE((SELECT MAX(rowid) FROM friend_tracking_events WHERE tenant_id=?),0)
                    AS tracking_revision,
                COALESCE((SELECT MAX(rowid) FROM event_anomalies WHERE tenant_id=?),0)
                    AS anomaly_revision,
                COALESCE((SELECT MAX(updated_at) FROM friends WHERE tenant_id=?),'')
                    AS friend_revision,
                COALESCE((SELECT MAX(fetched_at) FROM world_cache),'')
                    AS world_cache_revision,
                COALESCE((SELECT MAX(updated_at) FROM world_resolution_state),'')
                    AS resolver_revision""",
                (
                    tenant_id,
                    tenant_id,
                    tenant_id,
                    tenant_id,
                    tenant_id,
                ),
            ).fetchone()
        return {
            "friends": friends,
            "selection": tuple(friend_ids),
            "revision": tuple(revision) if revision else (),
        }

    def _snapshot(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        scope: dict[str, Any],
    ) -> dict[str, Any]:
        start_key = self._iso(start)
        end_key = self._iso(end)
        sample_start_key = self._iso(start - timedelta(seconds=7260))
        friends = scope["friends"]
        friend_ids = list(scope["selection"])
        with self.store.lock, self.store.connection() as db:
            self.store._require_tenant(db, tenant_id)

            events: list[dict[str, Any]] = []
            tracking: list[dict[str, Any]] = []
            if friend_ids:
                placeholders = ",".join("?" for _ in friend_ids)
                event_select = self._event_select()
                event_columns = (
                    "client_event_id,friend_id,occurred_at,old_status,new_status,"
                    "location,platform,source,anomaly,anomaly_reason"
                )
                events = [
                    dict(row)
                    for row in db.execute(
                        f"""WITH prior AS (
                            SELECT {event_select},ROW_NUMBER() OVER (
                                PARTITION BY e.friend_id
                                ORDER BY e.occurred_at DESC,e.client_event_id DESC
                            ) AS rank
                            FROM status_events e
                            WHERE e.tenant_id=? AND e.friend_id IN ({placeholders})
                              AND e.occurred_at<?
                              AND NOT EXISTS(SELECT 1 FROM event_anomalies a
                                  WHERE a.tenant_id=e.tenant_id
                                    AND a.event_kind='status_event'
                                    AND a.event_id=e.client_event_id)
                        ),windowed AS (
                            SELECT {event_select} FROM status_events e
                            WHERE e.tenant_id=? AND e.friend_id IN ({placeholders})
                              AND e.occurred_at>=? AND e.occurred_at<?
                        )
                        SELECT {event_columns} FROM windowed
                        UNION ALL
                        SELECT {event_columns} FROM prior WHERE rank=1
                        ORDER BY friend_id,occurred_at,client_event_id""",
                        (
                            tenant_id,
                            *friend_ids,
                            start_key,
                            tenant_id,
                            *friend_ids,
                            start_key,
                            end_key,
                        ),
                    ).fetchall()
                ]
                tracking = [
                    dict(row)
                    for row in db.execute(
                        f"""WITH prior AS (
                            SELECT event_id,friend_id,tracked,occurred_at,source,
                            ROW_NUMBER() OVER (
                                PARTITION BY friend_id
                                ORDER BY occurred_at DESC,event_id DESC
                            ) AS rank
                            FROM friend_tracking_events
                            WHERE tenant_id=? AND friend_id IN ({placeholders})
                              AND occurred_at<?
                        ),windowed AS (
                            SELECT event_id,friend_id,tracked,occurred_at,source
                            FROM friend_tracking_events
                            WHERE tenant_id=? AND friend_id IN ({placeholders})
                              AND occurred_at>=? AND occurred_at<?
                        )
                        SELECT event_id,friend_id,tracked,occurred_at,source
                        FROM windowed
                        UNION ALL
                        SELECT event_id,friend_id,tracked,occurred_at,source
                        FROM prior WHERE rank=1
                        ORDER BY friend_id,occurred_at,event_id""",
                        (
                            tenant_id,
                            *friend_ids,
                            start_key,
                            tenant_id,
                            *friend_ids,
                            start_key,
                            end_key,
                        ),
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
        return {
            "friends": friends,
            "events": events,
            "tracking": tracking,
            "samples": samples,
            "selection": scope["selection"],
            "revision": scope["revision"],
        }

    def _earliest_observation(
        self,
        tenant_id: str,
        scope: dict[str, Any],
        end: datetime,
    ) -> datetime:
        end_key = self._iso(end)
        candidates: list[str] = []
        sample_value = ""
        friend_ids = list(scope["selection"])
        with self.store.lock, self.store.connection() as db:
            self.store._require_tenant(db, tenant_id)
            sample = db.execute(
                """SELECT MIN(observed_at) AS value FROM collection_samples
                WHERE tenant_id=? AND authoritative=1 AND outcome='success'
                  AND observed_at<?""",
                (tenant_id, end_key),
            ).fetchone()
            if sample and sample["value"]:
                sample_value = str(sample["value"])
            if friend_ids:
                placeholders = ",".join("?" for _ in friend_ids)
                event = db.execute(
                    f"""SELECT MIN(e.occurred_at) AS value FROM status_events e
                    WHERE e.tenant_id=? AND e.friend_id IN ({placeholders})
                      AND e.occurred_at<?
                      AND NOT EXISTS(SELECT 1 FROM event_anomalies a
                          WHERE a.tenant_id=e.tenant_id
                            AND a.event_kind='status_event'
                            AND a.event_id=e.client_event_id)""",
                    (tenant_id, *friend_ids, end_key),
                ).fetchone()
                tracking = db.execute(
                    f"""SELECT MIN(occurred_at) AS value
                    FROM friend_tracking_events
                    WHERE tenant_id=? AND friend_id IN ({placeholders})
                      AND occurred_at<?""",
                    (tenant_id, *friend_ids, end_key),
                ).fetchone()
                for row in (event, tracking):
                    if row and row["value"]:
                        candidates.append(str(row["value"]))

        if not candidates and sample_value:
            candidates.append(sample_value)

        parsed: list[datetime] = []
        for value in candidates:
            try:
                item = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
            if item.tzinfo is None:
                item = item.replace(tzinfo=timezone.utc)
            normalized = item.astimezone(timezone.utc)
            if normalized < end:
                parsed.append(normalized)
        return min(parsed) if parsed else end - timedelta(days=1)

    @staticmethod
    def _empty_world_stats() -> dict[str, Any]:
        return {
            "minutes": 0.0,
            "unique_people": 0,
            "visit_count": 0,
            "return_visits": 0,
            "last_observed": None,
        }

    @staticmethod
    def _visit_was_interrupted(
        events: list[dict[str, Any]],
        previous_end: datetime,
        current_start: datetime,
        location: str,
    ) -> bool:
        for event in events:
            if bool(event.get("anomaly")) or event.get("anomaly_reason"):
                continue
            try:
                occurred_at = datetime.fromisoformat(
                    str(event.get("occurred_at") or "").replace("Z", "+00:00")
                )
            except ValueError:
                continue
            if occurred_at.tzinfo is None:
                occurred_at = occurred_at.replace(tzinfo=timezone.utc)
            occurred_at = occurred_at.astimezone(timezone.utc)
            if not (previous_end < occurred_at <= current_start):
                continue
            if str(event.get("new_status") or "").lower() == "offline":
                return True
            if str(event.get("location") or "") != location:
                return True
        return False

    @classmethod
    def _aggregate(
        cls,
        snapshot: dict[str, Any],
        covered: list[TimeWindow],
        start: datetime,
        end: datetime,
    ) -> tuple[dict[str, dict[str, Any]], float]:
        events_by_friend = cls._group_by_friend(snapshot["events"])
        tracking_by_friend = cls._group_by_friend(snapshot["tracking"])
        accumulated: dict[str, dict[str, Any]] = {}
        visitors: dict[str, set[str]] = defaultdict(set)
        unavailable_minutes = 0.0
        for friend in snapshot["friends"]:
            friend_id = str(friend["id"])
            friend_events = events_by_friend.get(friend_id, [])
            tracked = build_tracking_windows(
                tracking_by_friend.get(friend_id, []),
                range_start=start,
                range_end=end,
            )
            person_coverage = intersect_windows(covered, tracked)
            spans = build_world_spans(
                friend_events,
                person_coverage,
                start,
                end,
                is_self=bool(friend["is_self"]),
            )
            previous_span: Any | None = None
            for span in spans:
                if span.location_kind != "world":
                    unavailable_minutes += span.window.minutes
                    previous_span = span
                    continue
                try:
                    world_id = require_world_id(span.world_id)
                except ValueError:
                    unavailable_minutes += span.window.minutes
                    previous_span = span
                    continue
                stats = accumulated.setdefault(
                    world_id,
                    {
                        "minutes": 0.0,
                        "visit_count": 0,
                        "last_observed": span.end,
                    },
                )
                stats["minutes"] += span.window.minutes
                continued_visit = (
                    previous_span is not None
                    and previous_span.location_kind == "world"
                    and previous_span.world_id == span.world_id
                    and previous_span.location == span.location
                    and not cls._visit_was_interrupted(
                        friend_events,
                        previous_span.end,
                        span.start,
                        span.location,
                    )
                )
                if not continued_visit:
                    stats["visit_count"] += 1
                stats["last_observed"] = max(stats["last_observed"], span.end)
                visitors[world_id].add(friend_id)
                previous_span = span

        result: dict[str, dict[str, Any]] = {}
        for world_id, stats in accumulated.items():
            unique_people = len(visitors[world_id])
            visit_count = int(stats["visit_count"])
            result[world_id] = {
                "minutes": round(float(stats["minutes"]), 1),
                "unique_people": unique_people,
                "visit_count": visit_count,
                "return_visits": max(0, visit_count - unique_people),
                "last_observed": cls._iso(stats["last_observed"]),
            }
        return result, round(unavailable_minutes, 1)

    @staticmethod
    def _last_observed_number(value: object) -> float:
        if not value:
            return 0.0
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return 0.0
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()

    @classmethod
    def _hot_sort_key(cls, item: dict[str, Any]) -> tuple[Any, ...]:
        return (
            -int(item["unique_people"]),
            -int(item["visit_count"]),
            -float(item["minutes"]),
            -cls._last_observed_number(item["last_observed"]),
            str(item["world_id"]),
        )

    @classmethod
    def _rising_sort_key(cls, item: dict[str, Any]) -> tuple[Any, ...]:
        return (
            -float(item["delta"]["minutes"]),
            -int(item["delta"]["unique_people"]),
            -int(item["delta"]["visit_count"]),
            -cls._last_observed_number(item["current"]["last_observed"]),
            str(item["world_id"]),
        )

    def _world_presentation(self, tenant_id: str, world_id: str) -> dict[str, Any]:
        cached = self.store.world_cache_get(world_id)
        fresh = self.store.world_cache_get(
            world_id, max_age_seconds=WORLD_CACHE_SECONDS
        )
        if cached is not None:
            stale = fresh is None
            if stale:
                self.resolver.enqueue(tenant_id, world_id)
            return {
                **normalize_world(world_id, cached),
                "resolution_status": "ready",
                "stale": stale,
            }
        self.resolver.enqueue(tenant_id, world_id)
        return {
            **normalize_world(world_id, {}),
            "resolution_status": "pending"
            if self.resolver.is_pending(world_id)
            else _public_resolution_status(self.resolver._state(world_id)),
            "stale": False,
        }

    @staticmethod
    def _coverage_payload(
        covered: list[TimeWindow], start: datetime, end: datetime
    ) -> dict[str, Any]:
        summary = coverage_summary(covered, range_start=start, range_end=end)
        return {
            "range_minutes": round(summary.expected_minutes, 1),
            "covered_minutes": round(summary.observed_minutes, 1),
            "ratio": round(summary.ratio, 4),
            "first_recorded": DiscoveryService._iso(summary.first_observed)
            if summary.first_observed
            else None,
            "last_recorded": DiscoveryService._iso(summary.last_observed)
            if summary.last_observed
            else None,
            "gaps": [
                {
                    "from": DiscoveryService._iso(gap.start),
                    "to": DiscoveryService._iso(gap.end),
                    "minutes": round(gap.minutes, 1),
                }
                for gap in summary.gaps
            ],
        }

    def _build_response(
        self,
        days: int,
        start: datetime,
        end: datetime,
        previous_start: datetime,
        snapshot: dict[str, Any],
        world_tag: str,
    ) -> dict[str, Any]:
        current_coverage = build_observed_windows(
            snapshot["samples"], range_start=start, range_end=end
        )
        previous_coverage = build_observed_windows(
            snapshot["samples"], range_start=previous_start, range_end=start
        )
        current, unavailable_minutes = self._aggregate(
            snapshot, current_coverage, start, end
        )
        previous, previous_unavailable_minutes = self._aggregate(
            snapshot, previous_coverage, previous_start, start
        )

        selected_tag = str(world_tag or "").strip()
        selected_worlds = {
            world_id
            for world_id in current
            if not selected_tag
            or world_has_metadata_tag(
                self.store.world_cache_get(world_id), selected_tag
            )
        }
        hot = [
            {
                "world_id": world_id,
                **current[world_id],
            }
            for world_id in selected_worlds
        ]
        hot.sort(key=self._hot_sort_key)
        for rank, item in enumerate(hot, 1):
            item["rank"] = rank

        rising: list[dict[str, Any]] = []
        for world_id in selected_worlds:
            current_stats = current[world_id]
            previous_stats = previous.get(world_id, self._empty_world_stats())
            delta = {
                "minutes": round(
                    float(current_stats["minutes"])
                    - float(previous_stats["minutes"]),
                    1,
                ),
                "unique_people": int(current_stats["unique_people"])
                - int(previous_stats["unique_people"]),
                "visit_count": int(current_stats["visit_count"])
                - int(previous_stats["visit_count"]),
            }
            if not any(value > 0 for value in delta.values()):
                continue
            rising.append(
                {
                    "world_id": world_id,
                    "current": current_stats,
                    "previous": previous_stats,
                    "delta": delta,
                }
            )
        rising.sort(key=self._rising_sort_key)
        for rank, item in enumerate(rising, 1):
            item["rank"] = rank

        return {
            "hot": hot,
            "rising": rising,
            "unavailable_minutes": unavailable_minutes,
            "previous_unavailable_minutes": previous_unavailable_minutes,
            "range": {
                "days": days,
                "from": self._iso(start),
                "to": self._iso(end),
                "previous_from": self._iso(previous_start),
                "previous_to": self._iso(start),
            },
            "coverage": self._coverage_payload(current_coverage, start, end),
            "selected_people": len(snapshot["friends"]),
            "world_tag": str(world_tag or "").strip() or None,
            "ranking": {
                "hot": [
                    "unique_people:desc",
                    "visit_count:desc",
                    "minutes:desc",
                    "last_observed:desc",
                    "world_id:asc",
                ],
                "rising": [
                    "delta.minutes:desc",
                    "delta.unique_people:desc",
                    "delta.visit_count:desc",
                    "current.last_observed:desc",
                    "world_id:asc",
                ],
            },
        }

    def _present_page(
        self,
        tenant_id: str,
        payload: dict[str, Any],
        *,
        limit: int,
        offset: int,
        world_ids: list[str] | None = None,
        hot_sort: str = "people",
        sort_direction: str = "desc",
    ) -> dict[str, Any]:
        selected_worlds = {str(value) for value in (world_ids or [])}
        hot_ranked = [
            copy.deepcopy(item)
            for item in payload["hot"]
            if not selected_worlds or str(item["world_id"]) in selected_worlds
        ]
        hot_sort_keys = {
            "people": lambda item: (
                int(item.get("unique_people") or 0),
                int(item.get("visit_count") or 0),
                float(item.get("minutes") or 0),
            ),
            "minutes": lambda item: (
                float(item.get("minutes") or 0),
                int(item.get("unique_people") or 0),
            ),
            "visits": lambda item: (
                int(item.get("visit_count") or 0),
                float(item.get("minutes") or 0),
            ),
            "recent": lambda item: self._last_observed_number(
                item.get("last_observed")
            ),
        }
        if hot_sort not in hot_sort_keys:
            raise ValueError("世界排序方式无效")
        hot_ranked.sort(
            key=hot_sort_keys[hot_sort],
            reverse=sort_direction != "asc",
        )
        for rank, item in enumerate(hot_ranked, 1):
            item["rank"] = rank
        rising_ranked = payload["rising"]
        hot_page = hot_ranked[offset : offset + limit]
        rising_page = rising_ranked[offset : offset + limit]
        presentations: dict[str, dict[str, Any]] = {}

        def present(item: dict[str, Any]) -> dict[str, Any]:
            world_id = str(item["world_id"])
            if world_id not in presentations:
                presentations[world_id] = self._world_presentation(
                    tenant_id, world_id
                )
            return {**presentations[world_id], **copy.deepcopy(item)}

        response = {
            key: copy.deepcopy(value)
            for key, value in payload.items()
            if key not in {"hot", "rising"}
        }
        response.update(
            {
                "hot": [present(item) for item in hot_page],
                "rising": [present(item) for item in rising_page],
                "hot_total": len(hot_ranked),
                "rising_total": len(rising_ranked),
                "limit": limit,
                "offset": offset,
            }
        )
        return response

    def discover(
        self,
        tenant_id: str,
        days: int,
        friend_id: str = "",
        friend_ids: list[str] | None = None,
        world_tag: str = "",
        world_ids: list[str] | None = None,
        hot_sort: str = "people",
        sort_direction: str = "desc",
        include_self: bool = True,
        limit: int = 30,
        offset: int = 0,
        allow_custom_range: bool = False,
    ) -> dict[str, Any]:
        selected_days = int(days)
        if selected_days < 0 or selected_days > 730 or (
            not allow_custom_range and selected_days not in self.ALLOWED_DAYS
        ):
            raise ValueError("范围仅支持 1 天、7 天、30 天或全部")
        page_size = max(1, min(int(limit), 100))
        page_offset = max(0, int(offset))
        end = self._current_time()
        scope = self._scope(
            tenant_id,
            friend_id=str(friend_id or ""),
            friend_ids=friend_ids,
            include_self=bool(include_self),
        )
        start = (
            self._earliest_observation(tenant_id, scope, end)
            if selected_days == 0
            else end - timedelta(days=selected_days)
        )
        previous_start = start - (end - start)
        cache_key = (
            tenant_id,
            selected_days,
            str(friend_id or ""),
            str(world_tag or "").strip().casefold(),
            bool(include_self),
            int(end.timestamp() // 60),
            scope["selection"],
            *scope["revision"],
        )
        current_monotonic = time.monotonic()
        cached_payload: dict[str, Any] | None = None
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached and cached[0] > current_monotonic:
                cached_payload = cached[1]
        if cached_payload is not None:
            return self._present_page(
                tenant_id,
                cached_payload,
                limit=page_size,
                offset=page_offset,
                world_ids=world_ids,
                hot_sort=hot_sort,
                sort_direction=sort_direction,
            )

        snapshot = self._snapshot(
            tenant_id,
            previous_start,
            end,
            scope,
        )
        payload = self._build_response(
            selected_days,
            start,
            end,
            previous_start,
            snapshot,
            str(world_tag or ""),
        )
        with self._cache_lock:
            if len(self._cache) >= self._CACHE_LIMIT:
                oldest_key = min(self._cache, key=lambda key: self._cache[key][0])
                self._cache.pop(oldest_key, None)
            self._cache[cache_key] = (
                current_monotonic + self._CACHE_SECONDS,
                copy.deepcopy(payload),
            )
        return self._present_page(
            tenant_id,
            payload,
            limit=page_size,
            offset=page_offset,
            world_ids=world_ids,
            hot_sort=hot_sort,
            sort_direction=sort_direction,
        )
