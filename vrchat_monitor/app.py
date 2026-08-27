from __future__ import annotations

import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .backup_io import MAX_JSON_BACKUP_BYTES, decode_backup_upload, encode_backup_gzip
from .db import Database
from .http_assets import static_asset_for_request
from .vrchat import CredentialStore, VRChatClient, VRChatError, event_to_friend, world_id_from_location


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DATA_DIR = Path(os.environ.get("VRCHAT_MONITOR_DATA", Path.home() / ".picoworks-vrchat-monitor"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "monitor.sqlite3"
COOKIE_PATH = DATA_DIR / "session.cookie"
SLEEP_GAP_SECONDS = 90


class Monitor:
    def __init__(self) -> None:
        self.db = Database(str(DB_PATH))
        self.db.backfill_current_snapshots()
        self.client = VRChatClient(CredentialStore(str(COOKIE_PATH)))
        self.client.set_raw_sink(self._record_raw_fetch)
        self.stop_event = threading.Event()
        self.ws_stop = threading.Event()
        self._lock = threading.RLock()
        self._status = "已停止"
        self._message = "准备就绪"
        self._last_error = ""
        self._next_sync = 0.0
        self._backoff = 0.0
        self._last_loop_at = time.monotonic()
        self._thread = threading.Thread(target=self._loop, name="vrchat-monitor", daemon=True)
        self._ws_thread: threading.Thread | None = None
        self._world_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._world_lock = threading.RLock()

    def _record_raw_fetch(self, fetch: dict[str, Any]) -> None:
        self.db.record_raw_fetch(
            method=str(fetch.get("method") or "GET"),
            path=str(fetch.get("path") or ""),
            status_code=fetch.get("status_code"),
            content_type=str(fetch.get("content_type") or ""),
            body=fetch.get("body") if isinstance(fetch.get("body"), bytes) else b"",
            error=str(fetch.get("error") or ""),
        )

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def login(self, username: str, password: str) -> dict[str, Any]:
        result = self.client.login(username.strip(), password)
        if result.get("requires_2fa"):
            with self._lock:
                self._status = "等待二次验证"
                self._message = "请输入 VRChat 验证器代码"
            return result
        self._after_login()
        return result

    def verify(self, code: str) -> dict[str, Any]:
        result = self.client.verify_2fa(code.strip())
        self._after_login()
        return {"requires_2fa": False, "user": result}

    def _after_login(self) -> None:
        self.ws_stop.set()
        self.client.disconnect_pipeline()
        if self._ws_thread and self._ws_thread.is_alive():
            self._ws_thread.join(timeout=1)
        self.ws_stop = threading.Event()
        with self._lock:
            self._status = "正在同步"
            self._message = "首次拉取好友列表…"
            self._last_error = ""
            self._next_sync = 0
            self._backoff = 0

    def logout(self) -> None:
        self.ws_stop.set()
        self.client.disconnect_pipeline()
        self.client.logout()
        with self._lock:
            self._status = "未登录"
            self._message = "点击右上角连接 VRChat"
            self._last_error = ""

    def sync_now(self) -> dict[str, Any]:
        return self._sync("manual")

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            now = time.monotonic()
            gap = now - self._last_loop_at
            self._last_loop_at = now
            if gap >= SLEEP_GAP_SECONDS and self.client.logged_in:
                self._recover_after_sleep(gap)
            if self.client.logged_in and time.monotonic() >= self._next_sync:
                self._sync("api")
            if self.client.logged_in and (self._ws_thread is None or not self._ws_thread.is_alive()):
                self._ws_thread = self.client.start_pipeline(self._on_pipeline_event, self.ws_stop)
            self.stop_event.wait(1)

    def _recover_after_sleep(self, gap: float) -> None:
        """Reconnect and force one REST calibration after macOS wakes from sleep."""
        self.ws_stop.set()
        self.client.disconnect_pipeline()
        self.ws_stop = threading.Event()
        with self._lock:
            minutes = max(1, round(gap / 60))
            self._status = "系统已唤醒"
            self._message = f"检测到睡眠约 {minutes} 分钟，正在重连并补同步…"
            self._last_error = ""
            self._next_sync = 0
            self._backoff = 0

    def _sync(self, source: str) -> dict[str, Any]:
        run = self.db.begin_sync(source)
        try:
            friends = self.client.friends()
            current_user = dict(self.client.auth_user or {})
            try:
                current_user = self.client.user()
            except VRChatError:
                if not current_user.get("id"):
                    raise
            self_id = str(current_user.get("id") or "")
            records = list(friends)
            if self_id:
                records.append(current_user)
            changed = self.db.upsert_friends(records, source=source, self_id=self_id)
            self.db.mark_missing_offline({str(friend.get("id")) for friend in records}, source=source)
            self.db.finish_sync(run, "ok", len(friends) + (1 if self_id else 0))
            with self._lock:
                self._status = "实时监控中" if self._ws_thread and self._ws_thread.is_alive() else "API 校准中"
                self._message = f"已同步 {len(friends)} 位好友和自己，记录 {changed} 个状态变化"
                self._last_error = ""
                self._backoff = 0
                self._next_sync = time.monotonic() + (180 if self._ws_thread and self._ws_thread.is_alive() else 45)
            return {"ok": True, "friends": len(friends), "tracked": len(records), "changed": changed}
        except VRChatError as error:
            self.db.finish_sync(run, "error", error=error.args[0])
            delay = max(error.retry_after or 0, 45)
            if error.status == 429:
                self._backoff = min(900, max(delay, self._backoff * 2 or delay))
            else:
                self._backoff = min(300, max(delay, 45, self._backoff * 1.5))
            with self._lock:
                self._status = "限流退避" if error.status == 429 else "连接异常"
                self._message = "已自动退避，稍后重试"
                self._last_error = str(error)
                self._next_sync = time.monotonic() + self._backoff
            if error.status == 401:
                self.client.logout()
            return {"ok": False, "error": str(error), "status": error.status}

    def _on_pipeline_event(self, event: dict[str, Any]) -> None:
        friend = event_to_friend(event)
        if not friend:
            return
        changed = self.db.upsert_friends([friend], source="websocket")
        if changed:
            with self._lock:
                self._status = "实时监控中"
                self._message = f"收到实时更新：{friend.get('displayName') or friend.get('username') or friend.get('id')}"

    def world_info(self, world_id: str) -> dict[str, Any]:
        normalized = world_id_from_location(world_id)
        if not normalized:
            raise VRChatError("世界 ID 格式异常")
        now = time.monotonic()
        with self._world_lock:
            cached = self._world_cache.get(normalized)
            if cached and now - cached[0] < 3600:
                return cached[1]
        raw = self.client.world(normalized)
        result = {
            "id": normalized,
            "name": str(raw.get("name") or normalized),
            "description": str(raw.get("description") or ""),
            "thumbnail_url": str(raw.get("thumbnailImageUrl") or raw.get("imageUrl") or ""),
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
        with self._world_lock:
            self._world_cache[normalized] = (time.monotonic(), result)
        return result

    def state(self) -> dict[str, Any]:
        with self._lock:
            status = self._status
            message = self._message
            error = self._last_error
        user = self.client.auth_user
        if self.client.logged_in and user is None:
            try:
                user = self.client.user()
            except VRChatError as exc:
                error = str(exc)
        return {
            "authenticated": self.client.logged_in,
            "user": {
                "id": user.get("id"),
                "displayName": user.get("displayName"),
                "username": user.get("username"),
                "status": user.get("status"),
                "location": user.get("location"),
                "platform": user.get("platform"),
            } if user else None,
            "status": status if self.client.logged_in else "未登录",
            "message": message if self.client.logged_in else "连接 VRChat 后开始监控",
            "error": error,
            "friends": self.db.friends(),
            "events": self.db.recent_events(),
            "last_sync": self.db.last_sync(),
        }


monitor = Monitor()


class Handler(BaseHTTPRequestHandler):
    server_version = "PicoWorksVRChatMonitor/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _json(self, payload: Any, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _body(self, max_bytes: int = 1024 * 1024) -> dict[str, Any]:
        raw = self._raw_body(max_bytes)
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _raw_body(self, max_bytes: int) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > max_bytes:
            raise ValueError("request too large")
        raw = self.rfile.read(length) if length else b""
        if len(raw) != length:
            raise ValueError("request body incomplete")
        return raw

    def do_GET(self) -> None:
        if self.path == "/api/state":
            self._json(monitor.state())
            return
        if self.path.startswith("/api/raw-fetch"):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            fetch_id = query.get("id", [None])[0]
            if fetch_id is not None:
                try:
                    item = monitor.db.raw_fetch(int(fetch_id))
                except ValueError:
                    self._json({"error": "raw fetch id 格式异常"}, 400)
                    return
                if item is None:
                    self._json({"error": "raw fetch 不存在"}, 404)
                else:
                    self._json(item)
                return
            try:
                limit = int(query.get("limit", ["50"])[0])
            except ValueError:
                limit = 50
            path = query.get("path", [None])[0]
            self._json({"fetches": monitor.db.raw_fetches(limit, path)})
            return
        if self.path.startswith("/api/stats"):
            query = self.path.split("?", 1)[1] if "?" in self.path else ""
            params = dict(item.split("=", 1) for item in query.split("&") if "=" in item)
            try:
                days = int(params.get("days", "7"))
            except ValueError:
                days = 7
            self._json(monitor.db.stats(days))
            return
        if self.path.startswith("/api/bridge-events"):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            try:
                after_id = int(query.get("after_id", ["0"])[0])
                limit = int(query.get("limit", ["1000"])[0])
            except ValueError:
                self._json({"error": "桥接分页参数格式异常"}, 400)
                return
            self._json(
                monitor.db.bridge_events(
                    after_id,
                    query.get("checkpoint_event_id", [""])[0],
                    limit,
                )
            )
            return
        if self.path.startswith("/api/history"):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            try:
                offset = int(query.get("offset", ["0"])[0])
                limit = int(query.get("limit", ["25"])[0])
            except ValueError:
                self._json({"error": "分页参数格式异常"}, 400)
                return
            self._json(monitor.db.history_page(offset, limit, query.get("q", [""])[0]))
            return
        if self.path == "/api/world" or self.path.startswith("/api/world?"):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            world_id = query.get("world_id", [""])[0]
            try:
                self._json(monitor.world_info(world_id))
            except VRChatError as error:
                self._json({"error": str(error), "status": error.status}, error.status or 400)
            return
        if self.path.startswith("/api/world-presence"):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            day = query.get("day", [None])[0]
            try:
                self._json(monitor.db.world_presence_overview(day))
            except ValueError as error:
                self._json({"error": str(error)}, 400)
            return
        if self.path.startswith("/api/presence"):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            day = query.get("day", [None])[0]
            try:
                days = int(query.get("days", ["30"])[0])
            except ValueError:
                days = 30
            heatmap_from = query.get("heatmap_from", [None])[0]
            heatmap_to = query.get("heatmap_to", [None])[0]
            try:
                self._json(monitor.db.presence_overview(day, days, heatmap_from, heatmap_to))
            except ValueError as error:
                self._json({"error": str(error)}, 400)
            return
        if self.path == "/api/export.csv":
            raw = monitor.db.csv_export().encode("utf-8-sig")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="vrchat-status-events.csv"')
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if self.path == "/api/export.json":
            try:
                raw = encode_backup_gzip(monitor.db.json_export())
            except ValueError as error:
                self._json({"error": str(error)}, 503)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/gzip")
            self.send_header("Content-Disposition", 'attachment; filename="vrchat-monitor-backup.json.gz"')
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)
            return
        self._serve_static(self.path)

    def do_POST(self) -> None:
        try:
            if self.path == "/api/import.json":
                body = decode_backup_upload(self._raw_body(MAX_JSON_BACKUP_BYTES))
            else:
                body = self._body(1024 * 1024)
            if self.path == "/api/login":
                if not body.get("username") or not body.get("password"):
                    self._json({"error": "请输入账号和密码"}, 400)
                    return
                self._json(monitor.login(str(body["username"]), str(body["password"])))
                return
            if self.path == "/api/verify-2fa":
                if not body.get("code"):
                    self._json({"error": "请输入验证码"}, 400)
                    return
                self._json(monitor.verify(str(body["code"])))
                return
            if self.path == "/api/logout":
                monitor.logout()
                self._json({"ok": True})
                return
            if self.path == "/api/sync":
                self._json(monitor.sync_now())
                return
            if self.path == "/api/import.json":
                self._json({"ok": True, "imported": monitor.db.json_import(body)})
                return
            self._json({"error": "not found"}, 404)
        except VRChatError as error:
            self._json({"error": str(error), "status": error.status}, error.status or 500)
        except (ValueError, json.JSONDecodeError) as error:
            self._json({"error": str(error)}, 400)
        except Exception as error:
            self._json({"error": f"内部错误：{error}"}, 500)

    def _serve_static(self, request_path: str) -> None:
        asset = static_asset_for_request(STATIC, request_path)
        if asset is None:
            self.send_error(404)
            return
        target, content_type = asset
        try:
            raw = target.read_bytes()
        except (FileNotFoundError, IsADirectoryError, OSError):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(raw)


def main() -> None:
    port = int(os.environ.get("VRCHAT_MONITOR_PORT", "8842"))
    monitor.start()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"VRChat monitor running at {url}", flush=True)
    if os.environ.get("VRCHAT_MONITOR_NO_BROWSER") != "1":
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stop_event.set()
        monitor.ws_stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
