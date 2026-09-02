from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable

from vrchat_monitor.vrchat import VRChatClient, VRChatLoginResult

from .session_crypto import SessionCipher


class MemoryCredentialStore:
    def __init__(self, cookie: str = ""):
        self.cookie = cookie

    def save(self, cookie: str) -> None:
        self.cookie = cookie

    def load(self) -> str | None:
        return self.cookie or None

    def clear(self) -> None:
        self.cookie = ""


@dataclass(frozen=True)
class PendingLogin:
    encrypted_cookie: bytes
    methods: tuple[str, ...]
    expires_at: float


class VRChatAuthService:
    def __init__(
        self,
        cipher: SessionCipher,
        client_factory: Callable[[MemoryCredentialStore], VRChatClient] = VRChatClient,
        lifetime_seconds: int = 600,
    ):
        self.cipher = cipher
        self.client_factory = client_factory
        self.lifetime_seconds = max(60, int(lifetime_seconds))
        self._pending: dict[str, PendingLogin] = {}
        self._lock = threading.Lock()

    def begin(self, username: str, password: str) -> tuple[str | None, VRChatLoginResult]:
        client = self.client_factory(MemoryCredentialStore())
        result = client.begin_login(username, password)
        if not result.requires_2fa:
            return None, result
        pending_id = secrets.token_urlsafe(32)
        pending = PendingLogin(
            encrypted_cookie=self.cipher.encrypt_pending(pending_id, result.cookie),
            methods=result.methods,
            expires_at=time.monotonic() + self.lifetime_seconds,
        )
        with self._lock:
            self._prune_locked()
            self._pending[pending_id] = pending
        return pending_id, result

    def complete(self, pending_id: str, code: str) -> VRChatLoginResult:
        with self._lock:
            self._prune_locked()
            pending = self._pending.get(pending_id)
        if pending is None:
            raise ValueError("pending login expired")
        cookie = self.cipher.decrypt_pending(pending_id, pending.encrypted_cookie)
        client = self.client_factory(MemoryCredentialStore(cookie))
        method = next(
            (
                candidate
                for candidate in ("emailOtp", "totp", "otp")
                if candidate in pending.methods
            ),
            pending.methods[0] if pending.methods else "totp",
        )
        result = client.complete_2fa(cookie, code, method)
        with self._lock:
            self._pending.pop(pending_id, None)
        return result

    def cancel(self, pending_id: str) -> None:
        with self._lock:
            self._pending.pop(pending_id, None)

    def _prune_locked(self) -> None:
        current = time.monotonic()
        expired = [key for key, value in self._pending.items() if value.expires_at <= current]
        for key in expired:
            self._pending.pop(key, None)
