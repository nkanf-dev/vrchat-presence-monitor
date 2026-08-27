from __future__ import annotations

import secrets
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import DefaultDict
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response, status

from .settings import Settings


COOKIE_NAME = "presence_session"
SECURE_COOKIE_NAME = "__Host-presence_session"
PENDING_COOKIE_NAME = "vrchat_pending"
SECURE_PENDING_COOKIE_NAME = "__Host-vrchat_pending"


def bearer(request: Request) -> str:
    value = str(request.headers.get("authorization") or "")
    prefix = "Bearer "
    return value[len(prefix) :].strip() if value.startswith(prefix) else ""


def browser_token(request: Request) -> tuple[str, bool]:
    cookie = request.cookies.get(SECURE_COOKIE_NAME) or request.cookies.get(COOKIE_NAME) or ""
    if cookie:
        return cookie, False
    legacy = bearer(request)
    return legacy, bool(legacy)


def request_is_secure(request: Request, settings: Settings) -> bool:
    if settings.cookie_secure == "always":
        return True
    if settings.cookie_secure == "never":
        return False
    if request.url.scheme == "https":
        return True
    if settings.trust_proxy_headers:
        return str(request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip() == "https"
    return False


def set_browser_cookie(response: Response, request: Request, settings: Settings, token: str) -> None:
    secure = request_is_secure(request, settings)
    response.set_cookie(
        key=SECURE_COOKIE_NAME if secure else COOKIE_NAME,
        value=token,
        max_age=settings.session_days * 24 * 60 * 60,
        path="/",
        secure=secure,
        httponly=True,
        samesite="strict",
    )


def clear_browser_cookies(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict")
    response.delete_cookie(SECURE_COOKIE_NAME, path="/", secure=True, httponly=True, samesite="strict")


def pending_token(request: Request) -> str:
    return request.cookies.get(SECURE_PENDING_COOKIE_NAME) or request.cookies.get(PENDING_COOKIE_NAME) or ""


def set_pending_cookie(response: Response, request: Request, settings: Settings, token: str) -> None:
    secure = request_is_secure(request, settings)
    response.set_cookie(
        key=SECURE_PENDING_COOKIE_NAME if secure else PENDING_COOKIE_NAME,
        value=token,
        max_age=600,
        path="/",
        secure=secure,
        httponly=True,
        samesite="strict",
    )


def clear_pending_cookie(response: Response) -> None:
    response.delete_cookie(PENDING_COOKIE_NAME, path="/", httponly=True, samesite="strict")
    response.delete_cookie(
        SECURE_PENDING_COOKIE_NAME,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )


def require_same_origin(request: Request) -> None:
    fetch_site = str(request.headers.get("sec-fetch-site") or "").lower()
    if fetch_site == "cross-site":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="cross-site request rejected")
    origin = str(request.headers.get("origin") or "").strip()
    if not origin:
        return
    parsed = urlsplit(origin)
    request_host = str(request.headers.get("host") or request.url.netloc).lower()
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != request_host:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="origin rejected")


def client_address(request: Request, settings: Settings) -> str:
    if settings.trust_proxy_headers:
        cloudflare = str(request.headers.get("cf-connecting-ip") or "").strip()
        if cloudflare:
            return cloudflare
    return request.client.host if request.client else "unknown"


@dataclass
class LoginRateLimiter:
    attempts: int
    window_seconds: int

    def __post_init__(self) -> None:
        self._failures: DefaultDict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def allowed(self, address: str) -> bool:
        cutoff = time.monotonic() - self.window_seconds
        with self._lock:
            recent = [stamp for stamp in self._failures.get(address, []) if stamp >= cutoff]
            if recent:
                self._failures[address] = recent
            else:
                self._failures.pop(address, None)
            return len(recent) < self.attempts

    def fail(self, address: str) -> None:
        with self._lock:
            self._failures[address].append(time.monotonic())

    def clear(self, address: str) -> None:
        with self._lock:
            self._failures.pop(address, None)


@dataclass
class RequestRateLimiter:
    requests: int
    window_seconds: int

    def __post_init__(self) -> None:
        self._requests: DefaultDict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def consume(self, key: str) -> bool:
        cutoff = time.monotonic() - self.window_seconds
        with self._lock:
            recent = [stamp for stamp in self._requests.get(key, []) if stamp >= cutoff]
            if len(recent) >= self.requests:
                self._requests[key] = recent
                return False
            recent.append(time.monotonic())
            self._requests[key] = recent
            return True


def constant_time_secret(actual: str, expected: str) -> bool:
    return bool(actual and expected and secrets.compare_digest(actual, expected))
