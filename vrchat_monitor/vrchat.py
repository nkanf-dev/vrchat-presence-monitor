from __future__ import annotations

import base64
import hashlib
import http.client
import json
import logging
import os
import random
import re
import secrets
import socket
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


API_BASE = "https://api.vrchat.cloud/api/1"
PIPELINE_HOST = "pipeline.vrchat.cloud"
USER_AGENT = "PicoWorksVRChatMonitor/0.1 (contact: local@localhost)"
WORLD_ID_PATTERN = re.compile(r"wrld_[0-9a-f-]{36}", re.IGNORECASE)
LOGGER = logging.getLogger("presence_monitor.vrchat")


def secure_tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def world_id_from_location(location: str | None) -> str | None:
    match = WORLD_ID_PATTERN.search(str(location or ""))
    return match.group(0) if match else None


def presence_fields(presence: Any) -> dict[str, str]:
    """Convert VRChat's nested presence object into the friend shape we store."""
    if not isinstance(presence, dict):
        return {}
    status = str(presence.get("status") or "").strip().lower()
    world = str(presence.get("world") or "").strip()
    instance = str(presence.get("instance") or "").strip()
    platform = str(presence.get("platform") or "").strip()
    if status == "offline" or world.lower() == "offline" or instance.lower() == "offline":
        location = "offline"
    elif world.lower() in {"private", "traveling"} or instance.lower() in {"private", "traveling"}:
        location = world if world.lower() in {"private", "traveling"} else instance
    elif world and instance and world_id_from_location(instance):
        location = instance
    elif world and instance:
        location = f"{world}:{instance}"
    else:
        location = world or instance or ("online" if status else "")
    fields = {"presence_available": "1"}
    if status:
        fields.update({"status": status, "presence_status": status})
    if location:
        fields.update({"location": location, "presence_location": location})
    if platform:
        fields.update({"platform": platform, "presence_platform": platform})
    return fields


class VRChatError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


@dataclass(frozen=True)
class VRChatLoginResult:
    requires_2fa: bool
    cookie: str
    methods: tuple[str, ...] = ()
    user: dict[str, Any] | None = None


class CredentialStore:
    """Uses macOS Keychain when available; otherwise a chmod-600 local file."""
    def __init__(self, path: str):
        self.path = path
        self.service = "picoworks.vrchat-monitor"

    def save(self, cookie: str) -> None:
        if self._is_macos():
            try:
                subprocess.run(["security", "add-generic-password", "-a", "local", "-s", self.service, "-w", cookie, "-U"], check=False, capture_output=True, timeout=3)
                return
            except subprocess.TimeoutExpired:
                pass
        with open(self.path, "w", encoding="utf-8") as file:
            file.write(cookie)
        os.chmod(self.path, 0o600)

    def load(self) -> str | None:
        if self._is_macos():
            try:
                result = subprocess.run(["security", "find-generic-password", "-a", "local", "-s", self.service, "-w"], check=False, capture_output=True, text=True, timeout=2)
            except subprocess.TimeoutExpired:
                result = None
            return result.stdout.strip() or None if result else None
        try:
            with open(self.path, encoding="utf-8") as file:
                return file.read().strip() or None
        except FileNotFoundError:
            return None

    def clear(self) -> None:
        if self._is_macos():
            try:
                subprocess.run(["security", "delete-generic-password", "-a", "local", "-s", self.service], check=False, capture_output=True, timeout=3)
            except subprocess.TimeoutExpired:
                pass
        else:
            try:
                os.remove(self.path)
            except FileNotFoundError:
                pass

    @staticmethod
    def _is_macos() -> bool:
        return os.uname().sysname == "Darwin" and subprocess.call(["which", "security"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0


def _cookie_header(headers: Any) -> str:
    values = headers.get_all("Set-Cookie") or []
    cookies = []
    for value in values:
        cookies.append(value.split(";", 1)[0])
    return "; ".join(cookies)


def _merge_cookie_headers(*headers: str | None) -> str:
    values: dict[str, str] = {}
    for header in headers:
        for pair in str(header or "").split(";"):
            name, separator, value = pair.strip().partition("=")
            if separator and name:
                values[name] = value
    return "; ".join(f"{name}={value}" for name, value in values.items())


def _cookie_value(header: str | None, name: str) -> str:
    for pair in str(header or "").split(";"):
        key, separator, value = pair.strip().partition("=")
        if separator and key == name:
            return value
    return ""


class VRChatClient:
    def __init__(
        self,
        credential_store: CredentialStore,
        proxy_url: str | None = None,
    ):
        self.credential_store = credential_store
        self.cookie = credential_store.load()
        self.proxy_url = str(proxy_url or "").strip()
        self.auth_user: dict[str, Any] | None = None
        self.raw_sink: Callable[[dict[str, Any]], None] | None = None
        self._lock = threading.RLock()
        self._pipeline_lock = threading.RLock()
        self._pipeline_socket: ssl.SSLSocket | None = None

    def set_raw_sink(self, sink: Callable[[dict[str, Any]], None] | None) -> None:
        self.raw_sink = sink

    def _record_raw(
        self,
        method: str,
        path: str,
        status_code: int | None,
        content_type: str = "",
        body: bytes = b"",
        error: str = "",
    ) -> None:
        sink = self.raw_sink
        if sink is None:
            return
        try:
            sink({
                "method": method,
                "path": path,
                "status_code": status_code,
                "content_type": content_type,
                "body": body,
                "error": error,
            })
        except Exception:
            # Raw retention must never take down the monitor or affect API retries.
            return

    @property
    def logged_in(self) -> bool:
        return bool(self.cookie)

    def _proxy_url(self) -> str | None:
        if self.proxy_url:
            return self.proxy_url
        proxies = urllib.request.getproxies()
        return proxies.get("https") or proxies.get("http")

    def _open_url(self, request: urllib.request.Request, timeout: float):
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                proxies = (
                    {"http": self.proxy_url, "https": self.proxy_url}
                    if self.proxy_url
                    else urllib.request.getproxies()
                )
                opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
                return opener.open(request, timeout=timeout)
            except urllib.error.HTTPError:
                raise
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                last_error = error
                if attempt < 2:
                    time.sleep(0.35 * (attempt + 1) + random.random() * 0.35)
        raise last_error or urllib.error.URLError("网络连接失败")

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None, cookie: str | None = None) -> tuple[Any, str | None]:
        url = f"{API_BASE}{path}"
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method, headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            **({"Content-Type": "application/json"} if body is not None else {}),
            **({"Cookie": cookie or self.cookie} if cookie or self.cookie else {}),
        })
        try:
            with self._open_url(request, timeout=25) as response:
                raw = response.read()
                self._record_raw(method, path, getattr(response, "status", None), response.headers.get("Content-Type", ""), raw)
                parsed = json.loads(raw.decode("utf-8")) if raw else {}
                response_cookie = _cookie_header(response.headers)
                if response_cookie and cookie is None:
                    with self._lock:
                        self.cookie = _merge_cookie_headers(self.cookie, response_cookie)
                        self.credential_store.save(self.cookie)
                return parsed, response_cookie
        except urllib.error.HTTPError as error:
            retry_after = None
            try:
                retry_after = float(error.headers.get("Retry-After", ""))
            except ValueError:
                pass
            try:
                raw_detail = error.read()
                detail = raw_detail.decode("utf-8", errors="replace")
            except Exception:
                raw_detail = b""
                detail = error.reason
            self._record_raw(method, path, error.code, error.headers.get("Content-Type", ""), raw_detail, str(error.reason))
            raise VRChatError(f"VRChat API {error.code}: {detail[:300]}", error.code, retry_after) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            self._record_raw(method, path, None, error=str(error))
            raise VRChatError(f"网络连接失败：{error}") from error

    def begin_login(self, username: str, password: str) -> VRChatLoginResult:
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        request = urllib.request.Request(f"{API_BASE}/auth/user", method="GET", headers={
            "Authorization": f"Basic {token}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        })
        try:
            with self._open_url(request, timeout=25) as response:
                raw = response.read()
                self._record_raw("GET", "/auth/user", getattr(response, "status", None), response.headers.get("Content-Type", ""), raw)
                parsed = json.loads(raw.decode("utf-8"))
                cookie = _cookie_header(response.headers)
        except urllib.error.HTTPError as error:
            try:
                raw_detail = error.read()
            except Exception:
                raw_detail = b""
            self._record_raw("GET", "/auth/user", error.code, error.headers.get("Content-Type", ""), raw_detail, str(error.reason))
            LOGGER.warning("VRChat credential login rejected with HTTP %s", error.code)
            if error.code == 401:
                raise VRChatError("账号或密码不正确", 401) from error
            raise VRChatError(f"登录失败：HTTP {error.code}", error.code) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            self._record_raw("GET", "/auth/user", None, error=str(error))
            LOGGER.warning("VRChat credential login network failure: %s", type(error).__name__)
            raise VRChatError(f"网络连接失败：{error}") from error

        if parsed.get("requiresTwoFactorAuth"):
            LOGGER.info(
                "VRChat credential login requires verification methods=%s",
                ",".join(map(str, parsed.get("requiresTwoFactorAuth") or ("totp",))),
            )
            return VRChatLoginResult(
                requires_2fa=True,
                cookie=cookie,
                methods=tuple(parsed.get("requiresTwoFactorAuth") or ("totp",)),
            )
        if not cookie:
            LOGGER.warning("VRChat credential login returned no session cookie")
            raise VRChatError("登录响应没有返回会话，请稍后重试")
        return VRChatLoginResult(False, cookie, user=parsed)

    def complete_2fa(
        self,
        cookie: str,
        code: str,
        method: str = "totp",
    ) -> VRChatLoginResult:
        if not cookie:
            raise VRChatError("登录会话已过期，请重新登录", 401)
        normalized_method = str(method or "totp").strip().casefold()
        verify_path = {
            "emailotp": "/auth/twofactorauth/emailotp/verify",
            "otp": "/auth/twofactorauth/otp/verify",
            "totp": "/auth/twofactorauth/totp/verify",
        }.get(normalized_method, "/auth/twofactorauth/totp/verify")
        parsed, new_cookie = self._request(
            "POST", verify_path, {"code": code}, cookie
        )
        if not isinstance(parsed, dict) or parsed.get("verified") is False:
            raise VRChatError("验证码不正确", 401)
        merged = _merge_cookie_headers(cookie, new_cookie)
        previous = self.cookie
        try:
            self.cookie = merged
            user = self.user()
        finally:
            self.cookie = previous
        return VRChatLoginResult(False, merged, user=user)

    def login(self, username: str, password: str) -> dict[str, Any]:
        result = self.begin_login(username, password)
        if result.requires_2fa:
            self._pending_cookie = result.cookie
            return {"requires_2fa": True, "methods": list(result.methods)}
        self._finish_login(result.user or {}, result.cookie)
        return {"requires_2fa": False, "user": result.user or {}}

    def verify_2fa(self, code: str) -> dict[str, Any]:
        cookie = getattr(self, "_pending_cookie", "")
        if not cookie:
            raise VRChatError("登录会话已过期，请重新登录", 401)
        result = self.complete_2fa(cookie, code)
        self._finish_login(result.user or {}, result.cookie)
        return result.user or {}

    def _finish_login(self, user: dict[str, Any], cookie: str) -> None:
        with self._lock:
            self.cookie = cookie
            self.auth_user = user
            self.credential_store.save(cookie)

    def logout(self) -> None:
        self.disconnect_pipeline()
        with self._lock:
            self.cookie = None
            self.auth_user = None
            self.credential_store.clear()

    def disconnect_pipeline(self) -> None:
        """Interrupt the current pipeline socket so sleep/wake and logout recover quickly."""
        with self._pipeline_lock:
            sock = self._pipeline_socket
            self._pipeline_socket = None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def user(self) -> dict[str, Any]:
        if not self.cookie:
            raise VRChatError("尚未登录", 401)
        parsed, _ = self._request("GET", "/auth/user")
        parsed.update(presence_fields(parsed.get("presence")))
        self.auth_user = parsed
        return parsed

    def friends(self) -> list[dict[str, Any]]:
        if not self.cookie:
            raise VRChatError("尚未登录", 401)
        # VRChat currently treats offline=true as the offline-only collection.
        # Pull both collections and merge by user id so online friends are not lost.
        merged: dict[str, dict[str, Any]] = {}
        for include_offline in (False, True):
            offset = 0
            while True:
                value = "true" if include_offline else "false"
                page, _ = self._request("GET", f"/auth/user/friends?offset={offset}&n=100&offline={value}")
                if not isinstance(page, list):
                    raise VRChatError("好友列表响应格式异常")
                for friend in page:
                    friend_id = str(friend.get("id") or "")
                    if friend_id:
                        # Prefer the online record if a friend appears in both sets.
                        if friend_id not in merged or not include_offline:
                            merged[friend_id] = friend
                if len(page) < 100:
                    break
                offset += len(page)
                if offset > 10000:
                    break
        return list(merged.values())

    def world(self, world_id: str) -> dict[str, Any]:
        world_id = world_id_from_location(world_id) or ""
        if not world_id:
            raise VRChatError("世界 ID 格式异常")
        parsed, _ = self._request("GET", f"/worlds/{urllib.parse.quote(world_id, safe='')}")
        if not isinstance(parsed, dict):
            raise VRChatError("世界信息响应格式异常")
        return parsed

    def start_pipeline(
        self,
        on_event: Callable[[dict[str, Any]], None],
        stop_event: threading.Event,
        *,
        on_error: Callable[[Exception, float], None] | None = None,
        max_backoff_seconds: float = 300,
        thread_name: str = "vrchat-pipeline",
    ) -> threading.Thread:
        thread = threading.Thread(
            target=self._pipeline_loop,
            args=(on_event, stop_event, on_error, max_backoff_seconds),
            name=thread_name,
            daemon=True,
        )
        thread.start()
        return thread

    def _pipeline_loop(
        self,
        on_event: Callable[[dict[str, Any]], None],
        stop_event: threading.Event,
        on_error: Callable[[Exception, float], None] | None,
        max_backoff_seconds: float,
    ) -> None:
        # The pipeline endpoint is intentionally isolated here because VRChat can evolve
        # event shapes independently from the public REST API.
        maximum = max(30.0, float(max_backoff_seconds))
        backoff = 5.0
        while not stop_event.is_set() and self.cookie:
            started = time.monotonic()
            try:
                self._pipeline_once(on_event, stop_event)
                error: Exception = ConnectionError("WebSocket 连接已关闭")
            except Exception as caught:
                error = caught
            if stop_event.is_set() or not self.cookie:
                break
            connected_for = time.monotonic() - started
            if connected_for >= 60:
                backoff = 5.0
            retry_after = (
                float(error.retry_after or 0)
                if isinstance(error, VRChatError)
                else 0.0
            )
            delay = min(maximum, max(backoff, retry_after))
            delay = min(maximum, delay + random.uniform(0, min(10, delay / 4)))
            if on_error is not None:
                try:
                    on_error(error, delay)
                except Exception:
                    pass
            if isinstance(error, VRChatError) and error.status == 401:
                break
            if stop_event.wait(delay):
                break
            if connected_for < 60:
                backoff = min(maximum, max(10.0, backoff * 2))

    def _open_pipeline_tls(self) -> ssl.SSLSocket:
        last_error: Exception | None = None
        for attempt in range(2):
            raw: socket.socket | None = None
            try:
                context = secure_tls_context()
                proxy_url = self._proxy_url()
                proxy = urllib.parse.urlsplit(proxy_url) if proxy_url else None
                if proxy and proxy.scheme in {"http", "https"} and proxy.hostname:
                    raw = socket.create_connection((proxy.hostname, proxy.port or 8080), timeout=25)
                    connect_headers = [
                        f"CONNECT {PIPELINE_HOST}:443 HTTP/1.1",
                        f"Host: {PIPELINE_HOST}:443",
                        "Proxy-Connection: Keep-Alive",
                    ]
                    if proxy.username:
                        credentials = f"{urllib.parse.unquote(proxy.username)}:{urllib.parse.unquote(proxy.password or '')}".encode()
                        connect_headers.append(f"Proxy-Authorization: Basic {base64.b64encode(credentials).decode()}")
                    raw.sendall(("\r\n".join(connect_headers) + "\r\n\r\n").encode())
                    response = self._read_http_headers(raw)
                    first_line = response.split(b"\r\n", 1)[0]
                    if not first_line.startswith(b"HTTP/") or b" 200 " not in first_line:
                        raise VRChatError("WebSocket 代理 CONNECT 失败")
                else:
                    raw = socket.create_connection((PIPELINE_HOST, 443), timeout=25)
                return context.wrap_socket(raw, server_hostname=PIPELINE_HOST)
            except Exception as error:
                last_error = error
                if raw is not None:
                    try:
                        raw.close()
                    except OSError:
                        pass
                if attempt < 1:
                    time.sleep(0.5 + random.random() * 0.5)
        raise last_error or ConnectionError("WebSocket TLS 连接失败")

    def _pipeline_once(self, on_event: Callable[[dict[str, Any]], None], stop_event: threading.Event) -> None:
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        auth_token = _cookie_value(self.cookie, "auth")
        if not auth_token:
            raise VRChatError("VRChat 登录会话无效", 401)
        sock = self._open_pipeline_tls()
        with self._pipeline_lock:
            self._pipeline_socket = sock
        try:
            sock.settimeout(30)
            request = (
                f"GET /?authToken={urllib.parse.quote(auth_token, safe='')} HTTP/1.1\r\n"
                f"Host: {PIPELINE_HOST}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                f"Cookie: {self.cookie or ''}\r\nUser-Agent: {USER_AGENT}\r\n\r\n"
            ).encode()
            sock.sendall(request)
            response = self._read_http_headers(sock)
            first_line = response.split(b"\r\n", 1)[0]
            if b" 101 " not in first_line:
                status = None
                retry_after = None
                try:
                    status = int(first_line.split()[1])
                except (IndexError, ValueError):
                    pass
                for line in response.split(b"\r\n")[1:]:
                    name, separator, value = line.partition(b":")
                    if separator and name.strip().lower() == b"retry-after":
                        try:
                            retry_after = float(value.strip())
                        except ValueError:
                            pass
                raise VRChatError("WebSocket 握手失败", status, retry_after)
            while not stop_event.is_set():
                try:
                    opcode, payload = self._read_frame(sock)
                except socket.timeout:
                    continue
                if opcode == 0x8:
                    break
                if opcode == 0x9:
                    self._send_frame(sock, 0xA, payload)
                elif opcode == 0x1:
                    self._record_raw("WS", "/pipeline", 200, "application/json", payload)
                    try:
                        parsed = json.loads(payload.decode("utf-8"))
                        if isinstance(parsed, dict):
                            on_event(parsed)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
        finally:
            with self._pipeline_lock:
                if self._pipeline_socket is sock:
                    self._pipeline_socket = None
            try:
                sock.close()
            except OSError:
                pass

    @staticmethod
    def _read_http_headers(sock: socket.socket | ssl.SSLSocket) -> bytes:
        data = b""
        while b"\r\n\r\n" not in data and len(data) < 65536:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        return data

    @staticmethod
    def _recv_exact(sock: ssl.SSLSocket, length: int) -> bytes:
        payload = bytearray()
        while len(payload) < length:
            chunk = sock.recv(length - len(payload))
            if not chunk:
                raise ConnectionError("WebSocket 连接已关闭")
            payload.extend(chunk)
        return bytes(payload)

    @staticmethod
    def _read_frame(sock: ssl.SSLSocket) -> tuple[int, bytes]:
        first = VRChatClient._recv_exact(sock, 2)
        opcode = first[0] & 0x0F
        masked = first[1] & 0x80
        length = first[1] & 0x7F
        if length == 126:
            length = int.from_bytes(VRChatClient._recv_exact(sock, 2), "big")
        elif length == 127:
            length = int.from_bytes(VRChatClient._recv_exact(sock, 8), "big")
        mask = VRChatClient._recv_exact(sock, 4) if masked else b""
        payload = bytearray(VRChatClient._recv_exact(sock, length))
        if masked:
            for index in range(length):
                payload[index] ^= mask[index % 4]
        return opcode, bytes(payload)

    @staticmethod
    def _send_frame(sock: ssl.SSLSocket, opcode: int, payload: bytes) -> None:
        mask = secrets.token_bytes(4)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        length = len(payload)
        if length < 126:
            header = bytes([0x80 | opcode, 0x80 | length])
        elif length < 65536:
            header = bytes([0x80 | opcode, 0x80 | 126]) + length.to_bytes(2, "big")
        else:
            header = bytes([0x80 | opcode, 0x80 | 127]) + length.to_bytes(8, "big")
        sock.sendall(header + mask + masked)


def event_to_friend(event: dict[str, Any]) -> dict[str, Any] | None:
    """Best-effort normalisation for pipeline notifications and future event variants."""
    candidate = event.get("content") or event.get("data") or event
    if isinstance(candidate, str):
        try:
            candidate = json.loads(candidate)
        except json.JSONDecodeError:
            return None
    if not isinstance(candidate, dict):
        return None
    user = candidate.get("user") if isinstance(candidate.get("user"), dict) else candidate
    friend_id = str(user.get("id") or user.get("userId") or candidate.get("userId") or "")
    # Pipeline also carries notifications whose identifiers use not_/frq_.  They
    # are durable raw evidence, but they are not VRChat user identities.
    if not friend_id.startswith("usr_"):
        return None
    result = dict(user)
    result.update(presence_fields(result.get("presence") or candidate.get("presence")))
    result["id"] = friend_id
    for key in ("status", "statusDescription", "location", "platform", "last_platform", "displayName", "username"):
        if key in candidate and key not in result:
            result[key] = candidate[key]
    return result
