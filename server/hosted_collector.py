from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from vrchat_monitor.vrchat import VRChatClient, VRChatError

from .storage import Store
from .vrchat_auth import MemoryCredentialStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _normalize_friend(
    raw: dict[str, Any],
    self_id: str,
    old: dict[str, Any] | None,
    stamp: str,
) -> dict[str, Any] | None:
    friend_id = str(raw.get("id") or raw.get("userId") or "")
    if not friend_id:
        return None
    is_self = friend_id == self_id
    location = str(raw.get("location") or "").strip()
    raw_status = str(raw.get("status") or "").strip().lower()
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
    status = aliases.get(raw_status, "active" if location else "offline")
    if location.lower() == "offline" or (is_self and not location):
        status = "offline"
    username = str(raw.get("username") or raw.get("name") or friend_id)
    display_name = str(raw.get("displayName") or username)
    previous_status = str((old or {}).get("status") or "")
    changed = old is None or previous_status != status
    last_seen = (old or {}).get("last_seen")
    if status != "offline":
        last_seen = stamp
    links = raw.get("bioLinks") or []
    if not isinstance(links, list):
        links = []
    return {
        "id": friend_id,
        "username": username,
        "display_name": display_name,
        "is_self": is_self,
        "status": status,
        "status_description": str(raw.get("statusDescription") or ""),
        "location": location,
        "platform": str(raw.get("last_platform") or raw.get("platform") or ""),
        "avatar_url": str(
            raw.get("profilePicOverride")
            or raw.get("currentAvatarThumbnailImageUrl")
            or ""
        ),
        "avatar_image_url": str(raw.get("currentAvatarImageUrl") or ""),
        "bio": str(raw.get("bio") or ""),
        "bio_links": [str(link) for link in links[:32]],
        "last_seen": last_seen,
        "last_changed": stamp if changed else (old or {}).get("last_changed"),
        "updated_at": stamp,
    }


class HostedCollectorManager:
    def __init__(
        self,
        store: Store,
        poll_seconds: int = 180,
        concurrency: int = 3,
        max_backoff_seconds: int = 1800,
    ):
        self.store = store
        self.poll_seconds = max(45, int(poll_seconds))
        self.max_backoff_seconds = max(60, int(max_backoff_seconds))
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(concurrency)), thread_name_prefix="hosted-vrchat"
        )
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="hosted-collector", daemon=True)
        self._lock = threading.Lock()
        self._due: dict[str, float] = {}
        self._backoff: dict[str, float] = {}
        self._running: dict[str, Future[None]] = {}

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout=10)
        self._executor.shutdown(wait=False, cancel_futures=True)

    def wake(self, tenant_id: str) -> None:
        with self._lock:
            self._due[tenant_id] = 0
        self._wake.set()

    def disconnect(self, tenant_id: str) -> None:
        with self._lock:
            self._due.pop(tenant_id, None)
            self._backoff.pop(tenant_id, None)
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            accounts = {row["tenant_id"]: row for row in self.store.active_vrchat_accounts()}
            current = time.monotonic()
            with self._lock:
                for tenant_id, future in list(self._running.items()):
                    if future.done():
                        self._running.pop(tenant_id, None)
                for tenant_id, account in accounts.items():
                    due = self._due.setdefault(tenant_id, current + random.uniform(0, 3))
                    if due <= current and tenant_id not in self._running:
                        self._running[tenant_id] = self._executor.submit(self._sync, account)
                for tenant_id in set(self._due) - set(accounts):
                    self._due.pop(tenant_id, None)
                    self._backoff.pop(tenant_id, None)
            self._wake.wait(1)
            self._wake.clear()

    def _schedule(self, tenant_id: str, seconds: float) -> None:
        with self._lock:
            self._due[tenant_id] = time.monotonic() + max(1, seconds)
        self._wake.set()

    def _sync(self, account: dict[str, str]) -> None:
        tenant_id = account["tenant_id"]
        try:
            cookie = self.store.vrchat_account_cookie(tenant_id)
            if not cookie:
                return
            client = VRChatClient(MemoryCredentialStore(cookie))
            client.set_raw_sink(
                lambda fetch: self.store.record_raw_fetch(
                    tenant_id,
                    str(fetch.get("method") or "GET"),
                    str(fetch.get("path") or ""),
                    fetch.get("status_code"),
                    str(fetch.get("content_type") or ""),
                    fetch.get("body") if isinstance(fetch.get("body"), bytes) else b"",
                    str(fetch.get("error") or ""),
                )
            )
            current_user = client.user()
            user_id = str(current_user.get("id") or account["vrchat_user_id"])
            raw_friends = client.friends()
            old_states = self.store.friend_states(tenant_id)
            stamp = _now()
            normalized: list[dict[str, Any]] = []
            events: list[dict[str, Any]] = []
            for raw in [*raw_friends, current_user]:
                friend_id = str(raw.get("id") or raw.get("userId") or "")
                friend = _normalize_friend(raw, user_id, old_states.get(friend_id), stamp)
                if friend is None:
                    continue
                normalized.append(friend)
                old = old_states.get(friend["id"])
                old_tuple = (
                    str((old or {}).get("status") or "unknown"),
                    str((old or {}).get("location") or ""),
                    str((old or {}).get("platform") or ""),
                )
                new_tuple = (friend["status"], friend["location"], friend["platform"])
                if old is None or old_tuple != new_tuple:
                    identity = json.dumps(
                        [friend["id"], stamp, *new_tuple], separators=(",", ":")
                    ).encode()
                    events.append(
                        {
                            "client_event_id": "hosted_" + hashlib.sha256(identity).hexdigest(),
                            "friend_id": friend["id"],
                            "occurred_at": stamp,
                            "old_status": old_tuple[0],
                            "new_status": friend["status"],
                            "location": friend["location"],
                            "platform": friend["platform"],
                            "source": "hosted-vrchat",
                        }
                    )
            self.store.ingest(
                tenant_id,
                account["collector_id"],
                normalized,
                events,
                "hosted-vrchat",
            )
            self.store.mark_vrchat_account_result(tenant_id)
            self._backoff.pop(tenant_id, None)
            self._schedule(tenant_id, self.poll_seconds + random.uniform(0, 20))
        except VRChatError as error:
            if error.status == 401:
                self.store.mark_vrchat_account_result(
                    tenant_id, error="需要重新登录 VRChat", reconnect=True
                )
                self.disconnect(tenant_id)
                return
            previous = self._backoff.get(tenant_id, 30)
            delay = min(
                self.max_backoff_seconds,
                max(float(error.retry_after or 0), previous * 2, 45),
            )
            self._backoff[tenant_id] = delay
            message = "VRChat 暂时不可用" if error.status is None else f"VRChat API {error.status}"
            self.store.mark_vrchat_account_result(tenant_id, error=message)
            self._schedule(tenant_id, delay + random.uniform(0, 15))
        except Exception:
            previous = self._backoff.get(tenant_id, 30)
            delay = min(self.max_backoff_seconds, max(60, previous * 2))
            self._backoff[tenant_id] = delay
            self.store.mark_vrchat_account_result(tenant_id, error="采集暂时中断")
            self._schedule(tenant_id, delay + random.uniform(0, 15))
