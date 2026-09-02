from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from vrchat_monitor.vrchat import (
    VRChatClient,
    VRChatError,
    event_to_friend,
    world_id_from_location,
)

from .storage import Store
from .vrchat_auth import MemoryCredentialStore


WORLD_CACHE_SECONDS = 24 * 60 * 60


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _event_time(event: dict[str, Any]) -> str:
    candidate: Any = event.get("content") or event.get("data") or {}
    if isinstance(candidate, str):
        try:
            candidate = json.loads(candidate)
        except json.JSONDecodeError:
            candidate = {}
    values = [
        event.get("timestamp"),
        event.get("created_at"),
        event.get("createdAt"),
    ]
    if isinstance(candidate, dict):
        values.extend(
            [
                candidate.get("timestamp"),
                candidate.get("created_at"),
                candidate.get("createdAt"),
            ]
        )
    for value in values:
        if value in (None, ""):
            continue
        try:
            if isinstance(value, (int, float)) or str(value).isdigit():
                numeric = float(value)
                if numeric > 10_000_000_000:
                    numeric /= 1000
                parsed = datetime.fromtimestamp(numeric, tz=timezone.utc)
            else:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")
        except (ValueError, TypeError, OSError):
            continue
    return _now()


def _old_text(old: dict[str, Any] | None, key: str, default: str = "") -> str:
    return str((old or {}).get(key) or default)


def _raw_value(raw: dict[str, Any], *keys: str) -> tuple[bool, Any]:
    for key in keys:
        if key in raw and raw[key] is not None:
            return True, raw[key]
    return False, None


def _profile_text(
    raw: dict[str, Any],
    old: dict[str, Any] | None,
    old_key: str,
    *keys: str,
    default: str = "",
    partial: bool = False,
) -> str:
    present, value = _raw_value(raw, *keys)
    if present and (not partial or str(value or "").strip()):
        return str(value or "")
    return _old_text(old, old_key, default)


def _bio_links(
    raw: dict[str, Any], old: dict[str, Any] | None, partial: bool
) -> list[str]:
    present, value = _raw_value(raw, "bioLinks", "bio_links")
    if present and isinstance(value, list) and (value or not partial):
        return [str(link) for link in value[:32]]
    previous = (old or {}).get("bio_links")
    if isinstance(previous, str):
        try:
            previous = json.loads(previous)
        except json.JSONDecodeError:
            previous = []
    return [str(link) for link in previous[:32]] if isinstance(previous, list) else []


def _normalize_friend(
    raw: dict[str, Any],
    self_id: str,
    old: dict[str, Any] | None,
    stamp: str,
    *,
    event_type: str = "",
) -> dict[str, Any] | None:
    friend_id = str(raw.get("id") or raw.get("userId") or "")
    if not friend_id or not friend_id.startswith("usr_"):
        return None
    event_type = str(event_type or "").strip().lower()
    partial = bool(event_type)
    is_self = friend_id == self_id or bool((old or {}).get("is_self"))
    location_present, location_value = _raw_value(
        raw, "location", "presence_location"
    )
    status_present, status_value = _raw_value(raw, "status", "presence_status")
    platform_present, platform_value = _raw_value(
        raw, "last_platform", "platform", "presence_platform"
    )
    location = (
        str(location_value or "")
        if location_present
        else _old_text(old, "location")
    )
    raw_status = (
        str(status_value or "").strip().lower()
        if status_present
        else _old_text(old, "status", "offline").strip().lower()
    )
    platform = (
        str(platform_value or "")
        if platform_present
        else _old_text(old, "platform")
    )
    aliases = {
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
    if "offline" in event_type:
        status = "offline"
        location = "offline"
    else:
        status = aliases.get(raw_status, "active" if location else "offline")
        if "online" in event_type and status == "offline":
            status = "active"
        if "online" in event_type and not location:
            location = "online"
        if location.strip().lower() == "offline" or (is_self and not location.strip()):
            status = "offline"

    previous = old or {}
    old_tuple = (
        str(previous.get("status") or "offline"),
        str(previous.get("location") or ""),
        str(previous.get("platform") or ""),
    )
    new_tuple = (status, location, platform)
    changed = old is None or old_tuple != new_tuple
    last_seen = previous.get("last_seen")
    if status != "offline":
        last_seen = stamp
    username = _profile_text(
        raw, old, "username", "username", "name", default=friend_id, partial=partial
    )
    display_name = _profile_text(
        raw,
        old,
        "display_name",
        "displayName",
        "display_name",
        default=username,
        partial=partial,
    )
    return {
        "id": friend_id,
        "username": username or friend_id,
        "display_name": display_name or username or friend_id,
        "is_self": is_self,
        "status": status,
        "status_description": _profile_text(
            raw,
            old,
            "status_description",
            "statusDescription",
            "status_description",
            partial=partial,
        ),
        "location": location,
        "platform": platform,
        "avatar_url": _profile_text(
            raw,
            old,
            "avatar_url",
            "profilePicOverride",
            "currentAvatarThumbnailImageUrl",
            "avatarUrl",
            "avatar_url",
            partial=partial,
        ),
        "avatar_image_url": _profile_text(
            raw,
            old,
            "avatar_image_url",
            "currentAvatarImageUrl",
            "avatarImageUrl",
            "avatar_image_url",
            partial=partial,
        ),
        "bio": _profile_text(raw, old, "bio", "bio", partial=partial),
        "bio_links": _bio_links(raw, old, partial),
        "last_seen": last_seen,
        "last_changed": stamp if changed else previous.get("last_changed"),
        "updated_at": stamp,
    }


def _transition_event(
    friend: dict[str, Any],
    old: dict[str, Any] | None,
    stamp: str,
    source: str,
    raw_event: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    old_tuple = (
        str((old or {}).get("status") or "unknown"),
        str((old or {}).get("location") or ""),
        str((old or {}).get("platform") or ""),
    )
    new_tuple = (
        str(friend["status"]),
        str(friend["location"]),
        str(friend["platform"]),
    )
    if old is not None and old_tuple == new_tuple:
        return None
    canonical_event = json.dumps(
        raw_event or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    identity = json.dumps(
        [friend["id"], canonical_event, stamp, *new_tuple],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "client_event_id": f"{source}_" + hashlib.sha256(identity).hexdigest(),
        "friend_id": friend["id"],
        "occurred_at": stamp,
        "old_status": old_tuple[0],
        "new_status": friend["status"],
        "location": friend["location"],
        "platform": friend["platform"],
        "source": source,
    }


@dataclass
class _TenantSession:
    account: dict[str, str]
    credentials: MemoryCredentialStore
    client: VRChatClient
    stop_event: threading.Event
    credential_updated_at: str
    persisted_cookie: str
    pipeline_thread: threading.Thread | None = None

    @property
    def tenant_id(self) -> str:
        return self.account["tenant_id"]


class HostedCollectorManager:
    def __init__(
        self,
        store: Store,
        poll_seconds: int = 180,
        concurrency: int = 3,
        max_backoff_seconds: int = 1800,
    ):
        self.store = store
        self.poll_seconds = max(90, int(poll_seconds))
        self.max_backoff_seconds = max(60, int(max_backoff_seconds))
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(concurrency)), thread_name_prefix="hosted-vrchat"
        )
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name="hosted-collector", daemon=True
        )
        self._lock = threading.RLock()
        self._due: dict[str, float] = {}
        self._backoff: dict[str, float] = {}
        self._running: dict[str, Future[None]] = {}
        self._sessions: dict[str, _TenantSession] = {}
        self._blocked: set[str] = set()
        self._world_locks: dict[str, threading.Lock] = {}
        self._world_retry_at: dict[str, float] = {}
        self._world_observer: Callable[[str, list[str]], None] | None = None

    def set_world_observer(
        self, observer: Callable[[str, list[str]], None] | None
    ) -> None:
        self._world_observer = observer

    def _notify_worlds(self, tenant_id: str, friends: list[dict[str, Any]]) -> None:
        observer = self._world_observer
        if observer is None:
            return
        world_ids = sorted(
            {
                world_id
                for friend in friends
                if (world_id := world_id_from_location(str(friend.get("location") or "")))
            }
        )
        if not world_ids:
            return
        try:
            observer(tenant_id, world_ids)
        except Exception:
            # World enrichment is independent from presence collection.
            return

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            self._stop_session(session)
        if self._thread.is_alive():
            self._thread.join(timeout=10)
        self._executor.shutdown(wait=False, cancel_futures=True)

    def wake(self, tenant_id: str) -> bool:
        with self._lock:
            self._blocked.discard(tenant_id)
            self._due[tenant_id] = 0
        self._wake.set()
        return any(
            account["tenant_id"] == tenant_id
            for account in self.store.active_vrchat_accounts()
        )

    def disconnect(self, tenant_id: str) -> None:
        with self._lock:
            self._blocked.add(tenant_id)
            session = self._sessions.pop(tenant_id, None)
            self._due.pop(tenant_id, None)
            self._backoff.pop(tenant_id, None)
        if session is not None:
            self._stop_session(session)
        self._wake.set()

    @staticmethod
    def _stop_session(session: _TenantSession) -> None:
        session.stop_event.set()
        session.client.disconnect_pipeline()
        thread = session.pipeline_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)

    def _is_current(self, session: _TenantSession) -> bool:
        with self._lock:
            return self._sessions.get(session.tenant_id) is session

    def _raw_sink(self, tenant_id: str, fetch: dict[str, Any]) -> None:
        self.store.record_raw_fetch(
            tenant_id,
            str(fetch.get("method") or "GET"),
            str(fetch.get("path") or ""),
            fetch.get("status_code"),
            str(fetch.get("content_type") or ""),
            fetch.get("body") if isinstance(fetch.get("body"), bytes) else b"",
            str(fetch.get("error") or ""),
        )

    def _start_session(self, account: dict[str, str]) -> _TenantSession | None:
        tenant_id = account["tenant_id"]
        with self._lock:
            current = self._sessions.get(tenant_id)
            if current is not None:
                return current
            if tenant_id in self._blocked:
                return None
        try:
            cookie = self.store.vrchat_account_cookie(tenant_id)
        except ValueError:
            self.store.mark_vrchat_account_result(
                tenant_id, error="需要重新登录 VRChat", reconnect=True
            )
            return None
        if not cookie:
            return None
        credentials = MemoryCredentialStore(cookie)
        client = VRChatClient(credentials)
        client.set_raw_sink(lambda fetch: self._raw_sink(tenant_id, fetch))
        session = _TenantSession(
            account=account,
            credentials=credentials,
            client=client,
            stop_event=threading.Event(),
            credential_updated_at=str(account.get("credential_updated_at") or ""),
            persisted_cookie=cookie,
        )
        with self._lock:
            if tenant_id in self._blocked:
                return None
            existing = self._sessions.setdefault(tenant_id, session)
        if existing is not session:
            return existing
        session.pipeline_thread = client.start_pipeline(
            lambda event: self._on_pipeline_event(session, event),
            session.stop_event,
            on_error=lambda error, delay: self._on_pipeline_error(
                session, error, delay
            ),
            max_backoff_seconds=min(self.max_backoff_seconds, 600),
            thread_name=f"vrchat-pipeline-{tenant_id[-8:]}",
        )
        with self._lock:
            self._due.setdefault(tenant_id, time.monotonic() + random.uniform(0, 3))
        return session

    def _reconcile_sessions(self, accounts: dict[str, dict[str, str]]) -> None:
        with self._lock:
            sessions = dict(self._sessions)
        for tenant_id, session in sessions.items():
            account = accounts.get(tenant_id)
            revision = str((account or {}).get("credential_updated_at") or "")
            if account is None or revision != session.credential_updated_at:
                with self._lock:
                    if self._sessions.get(tenant_id) is session:
                        self._sessions.pop(tenant_id, None)
                self._stop_session(session)
        for tenant_id, account in accounts.items():
            with self._lock:
                exists = tenant_id in self._sessions or tenant_id in self._blocked
            if not exists:
                self._start_session(account)

    def _loop(self) -> None:
        while not self._stop.is_set():
            accounts = {
                row["tenant_id"]: row for row in self.store.active_vrchat_accounts()
            }
            self._reconcile_sessions(accounts)
            current = time.monotonic()
            with self._lock:
                for tenant_id, future in list(self._running.items()):
                    if future.done():
                        self._running.pop(tenant_id, None)
                sessions = dict(self._sessions)
                for tenant_id, session in sessions.items():
                    due = self._due.setdefault(
                        tenant_id, current + random.uniform(0, 3)
                    )
                    if due <= current and tenant_id not in self._running:
                        self._running[tenant_id] = self._executor.submit(
                            self._sync, session
                        )
                for tenant_id in set(self._due) - set(accounts):
                    self._due.pop(tenant_id, None)
                    self._backoff.pop(tenant_id, None)
            self._wake.wait(1)
            self._wake.clear()

    def _schedule(self, tenant_id: str, seconds: float) -> None:
        with self._lock:
            self._due[tenant_id] = time.monotonic() + max(1, seconds)
        self._wake.set()

    def _persist_rotated_cookie(self, session: _TenantSession) -> None:
        cookie = str(session.client.cookie or session.credentials.cookie or "")
        if not cookie or cookie == session.persisted_cookie:
            return
        revision = self.store.update_vrchat_cookie(session.tenant_id, cookie)
        session.persisted_cookie = cookie
        session.credential_updated_at = revision
        session.account["credential_updated_at"] = revision

    def _sync(self, session: _TenantSession) -> None:
        tenant_id = session.tenant_id
        if not self._is_current(session):
            return
        started = time.monotonic()
        try:
            current_user = session.client.user()
            user_id = str(
                current_user.get("id") or session.account["vrchat_user_id"]
            )
            raw_friends = session.client.friends()
            self._persist_rotated_cookie(session)
            stamp = _now()
            normalized: list[dict[str, Any]] = []
            events: list[dict[str, Any]] = []
            with self.store.lock:
                old_states = self.store.friend_states(tenant_id)
                for raw in [*raw_friends, current_user]:
                    friend_id = str(raw.get("id") or raw.get("userId") or "")
                    old = old_states.get(friend_id)
                    friend = _normalize_friend(raw, user_id, old, stamp)
                    if friend is None:
                        continue
                    normalized.append(friend)
                    event = _transition_event(
                        friend, old, stamp, "hosted-rest"
                    )
                    if event is not None:
                        events.append(event)
                if not self._is_current(session):
                    return
                self.store.ingest_authoritative_snapshot(
                    tenant_id,
                    session.account["collector_id"],
                    normalized,
                    events,
                    source="hosted-rest",
                    observed_at=stamp,
                    expected_interval_seconds=self.poll_seconds,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            self._notify_worlds(tenant_id, normalized)
            with self._lock:
                self._backoff.pop(tenant_id, None)
            self._schedule(tenant_id, self.poll_seconds + random.uniform(0, 20))
        except VRChatError as error:
            category = (
                "session_expired"
                if error.status == 401
                else "rate_limited"
                if error.status == 429
                else "network"
                if error.status is None
                else "upstream"
            )
            self.store.record_collection_failure(
                tenant_id,
                "hosted-rest",
                category,
                self.poll_seconds,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            if error.status == 401:
                self.store.mark_vrchat_account_result(
                    tenant_id, error="需要重新登录 VRChat", reconnect=True
                )
                self.disconnect(tenant_id)
                return
            with self._lock:
                previous = self._backoff.get(tenant_id, 30)
                delay = min(
                    self.max_backoff_seconds,
                    max(float(error.retry_after or 0), previous * 2, 60),
                )
                self._backoff[tenant_id] = delay
            message = (
                "VRChat 请求过于频繁，已自动退避"
                if error.status == 429
                else "VRChat 暂时不可用"
            )
            self.store.mark_vrchat_account_result(tenant_id, error=message)
            self._schedule(tenant_id, delay + random.uniform(0, min(20, delay / 4)))
        except Exception:
            self.store.record_collection_failure(
                tenant_id,
                "hosted-rest",
                "network",
                self.poll_seconds,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            with self._lock:
                previous = self._backoff.get(tenant_id, 30)
                delay = min(self.max_backoff_seconds, max(60, previous * 2))
                self._backoff[tenant_id] = delay
            self.store.mark_vrchat_account_result(
                tenant_id, error="采集暂时中断"
            )
            self._schedule(tenant_id, delay + random.uniform(0, 20))

    def _on_pipeline_error(
        self, session: _TenantSession, error: Exception, delay: float
    ) -> None:
        if not self._is_current(session):
            return
        if isinstance(error, VRChatError) and error.status == 401:
            self.store.mark_vrchat_account_result(
                session.tenant_id, error="需要重新登录 VRChat", reconnect=True
            )
            self.disconnect(session.tenant_id)
        elif isinstance(error, VRChatError) and error.status == 429:
            with self._lock:
                self._backoff[session.tenant_id] = max(
                    self._backoff.get(session.tenant_id, 0), delay
                )
            self._schedule(session.tenant_id, delay)

    def _on_pipeline_event(
        self, session: _TenantSession, event: dict[str, Any]
    ) -> None:
        if not self._is_current(session):
            return
        patch = event_to_friend(event)
        if patch is None:
            return
        stamp = _event_time(event)
        event_type = str(event.get("type") or "")
        friend_id = str(patch.get("id") or patch.get("userId") or "")
        with self.store.lock:
            old = self.store.friend_states(session.tenant_id).get(friend_id)
            friend = _normalize_friend(
                patch,
                session.account["vrchat_user_id"],
                old,
                stamp,
                event_type=event_type,
            )
            if friend is None or not self._is_current(session):
                return
            transition = _transition_event(
                friend, old, stamp, "hosted-pipeline", raw_event=event
            )
            self.store.ingest(
                session.tenant_id,
                session.account["collector_id"],
                [friend],
                [transition] if transition is not None else [],
                "hosted-pipeline",
            )
        self._notify_worlds(session.tenant_id, [friend])

    def fetch_world(self, tenant_id: str, world_id: str) -> dict[str, Any]:
        """Fetch one world through a tenant session for the background resolver."""
        normalized = world_id_from_location(world_id)
        if not normalized:
            raise VRChatError("世界 ID 格式异常", 400)
        with self._lock:
            session = self._sessions.get(tenant_id)
        if session is None:
            account = next(
                (
                    row
                    for row in self.store.active_vrchat_accounts()
                    if row["tenant_id"] == tenant_id
                ),
                None,
            )
            if account is not None:
                session = self._start_session(account)
        if session is None or not self._is_current(session):
            raise VRChatError("需要重新登录 VRChat", 401)
        try:
            raw = session.client.world(normalized)
            self._persist_rotated_cookie(session)
            return raw
        except VRChatError as error:
            if error.status == 401:
                self.store.mark_vrchat_account_result(
                    tenant_id, error="需要重新登录 VRChat", reconnect=True
                )
                self.disconnect(tenant_id)
            raise

    def world_info(self, tenant_id: str, world_id: str) -> dict[str, Any]:
        normalized = world_id_from_location(world_id)
        if not normalized:
            raise VRChatError("世界 ID 格式异常", 400)
        cached = self.store.world_cache_get(
            normalized, max_age_seconds=WORLD_CACHE_SECONDS
        )
        if cached is not None:
            return cached
        with self._lock:
            world_lock = self._world_locks.setdefault(normalized, threading.Lock())
        with world_lock:
            cached = self.store.world_cache_get(
                normalized, max_age_seconds=WORLD_CACHE_SECONDS
            )
            if cached is not None:
                return cached
            stale = self.store.world_cache_get(normalized)
            retry_at = self._world_retry_at.get(normalized, 0)
            if time.monotonic() < retry_at:
                if stale is not None:
                    return stale
                raise VRChatError(
                    "世界信息正在退避，请稍后再试",
                    429,
                    retry_at - time.monotonic(),
                )
            with self._lock:
                session = self._sessions.get(tenant_id)
            if session is None:
                account = next(
                    (
                        row
                        for row in self.store.active_vrchat_accounts()
                        if row["tenant_id"] == tenant_id
                    ),
                    None,
                )
                if account is not None:
                    session = self._start_session(account)
            if session is None or not self._is_current(session):
                if stale is not None:
                    return stale
                raise VRChatError("需要重新登录 VRChat", 401)
            try:
                raw = session.client.world(normalized)
                self._persist_rotated_cookie(session)
            except VRChatError as error:
                delay = min(
                    self.max_backoff_seconds,
                    max(float(error.retry_after or 0), 60 if error.status == 429 else 30),
                )
                self._world_retry_at[normalized] = time.monotonic() + delay
                if error.status == 401:
                    self.store.mark_vrchat_account_result(
                        tenant_id, error="需要重新登录 VRChat", reconnect=True
                    )
                    self.disconnect(tenant_id)
                if stale is not None:
                    return stale
                raise
            result = {
                "id": normalized,
                "name": str(raw.get("name") or normalized),
                "description": str(raw.get("description") or ""),
                "thumbnail_url": str(
                    raw.get("thumbnailImageUrl") or raw.get("imageUrl") or ""
                ),
                "image_url": str(raw.get("imageUrl") or ""),
                "author_id": str(raw.get("authorId") or ""),
                "capacity": raw.get("capacity"),
                "recommended_capacity": raw.get("recommendedCapacity"),
                "occupants": raw.get("occupants"),
                "visits": raw.get("visits"),
                "favorites": raw.get("favorites"),
                "popularity": raw.get("popularity"),
                "heat": raw.get("heat"),
                "release_status": str(raw.get("releaseStatus") or ""),
                "author_name": str(raw.get("authorName") or ""),
                "organization": str(raw.get("organization") or ""),
                "tags": raw.get("tags") if isinstance(raw.get("tags"), list) else [],
                "publication_date": str(raw.get("publicationDate") or ""),
                "created_at": str(raw.get("created_at") or raw.get("createdAt") or ""),
                "updated_at": str(raw.get("updated_at") or raw.get("updatedAt") or ""),
            }
            self.store.world_cache_put(normalized, result)
            with self._lock:
                self._world_retry_at.pop(normalized, None)
            return result
