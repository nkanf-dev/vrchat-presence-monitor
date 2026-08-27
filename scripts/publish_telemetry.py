#!/usr/bin/env python3
"""Publish normalized local snapshots to a Hosted instance without sharing VRChat credentials."""
from __future__ import annotations

import argparse
import json
import os
import random
import socket
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable


PageFetcher = Callable[[int, int], dict[str, Any]]


def _json_request(
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    token: str = "",
    attempts: int = 4,
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode() if payload is not None else None
    headers = {"Accept": "application/json", "User-Agent": "PresenceMonitorBridge/1"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")

    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))
                if not isinstance(result, dict):
                    raise RuntimeError("服务返回的 JSON 不是对象")
                return result
        except urllib.error.HTTPError as error:
            retryable = error.code == 429 or 500 <= error.code < 600
            if not retryable or attempt + 1 >= attempts:
                detail = error.read(2048).decode("utf-8", "replace")
                raise RuntimeError(f"HTTP {error.code}: {detail or error.reason}") from error
            retry_after = error.headers.get("Retry-After", "")
            try:
                delay = max(1.0, min(float(retry_after), 300.0))
            except ValueError:
                delay = min(30.0, 2.0**attempt + random.random())
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as error:
            if attempt + 1 >= attempts:
                raise RuntimeError(f"网络请求失败：{error}") from error
            time.sleep(min(30.0, 2.0**attempt + random.random()))
    raise RuntimeError("网络请求失败")


def _is_loopback(host: str | None) -> bool:
    return (host or "").lower() in {"127.0.0.1", "::1", "localhost"}


def validate_urls(local_url: str, remote_url: str, allow_non_loopback_local: bool = False) -> None:
    local = urllib.parse.urlsplit(local_url)
    remote = urllib.parse.urlsplit(remote_url)
    if local.scheme not in {"http", "https"} or not local.hostname:
        raise ValueError("本地服务地址必须是有效的 HTTP(S) URL")
    if not allow_non_loopback_local and not _is_loopback(local.hostname):
        raise ValueError("默认只允许读取本机采集器；远程采集器需显式使用 --allow-non-loopback-local")
    if remote.scheme != "https" and not _is_loopback(remote.hostname):
        raise ValueError("Hosted 地址必须使用 HTTPS")


def collect_events(fetch_page: PageFetcher, cursor: int) -> list[dict[str, Any]]:
    offset = 0
    collected: list[dict[str, Any]] = []
    while True:
        page = fetch_page(offset, 100)
        items = page.get("items") if isinstance(page.get("items"), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                event_id = int(item.get("id", 0))
            except (TypeError, ValueError):
                continue
            if event_id > cursor:
                collected.append(item)
        if not items or not bool(page.get("has_more")):
            break
        numeric_ids = [int(item.get("id", 0)) for item in items if isinstance(item, dict) and str(item.get("id", "")).isdigit()]
        if numeric_ids and min(numeric_ids) <= cursor:
            break
        offset += len(items)
    return sorted(collected, key=lambda item: int(item["id"]))


def normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    event_id = int(event["id"])
    return {
        "client_event_id": f"local-{event_id}",
        "friend_id": str(event.get("friend_id") or ""),
        "occurred_at": str(event.get("occurred_at") or ""),
        "old_status": str(event.get("old_status") or "unknown"),
        "new_status": str(event.get("new_status") or "offline"),
        "location": str(event.get("location") or ""),
        "platform": str(event.get("platform") or ""),
        "source": str(event.get("source") or "local-bridge"),
    }


def read_state(path: Path) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return max(0, int(payload.get("last_event_id", 0))) if isinstance(payload, dict) else 0
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
        return 0


def write_state(path: Path, last_event_id: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".bridge-state-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "last_event_id": last_event_id}, handle, separators=(",", ":"))
            handle.write("\n")
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def _read_token(value: str, file_name: str) -> str:
    if value and file_name:
        raise ValueError("collector token 只能通过参数或文件设置一种")
    token = Path(file_name).read_text(encoding="utf-8").strip() if file_name else value.strip()
    if not token:
        raise ValueError("缺少 collector token")
    return token


def publish(local_url: str, remote_url: str, token: str, state_path: Path) -> dict[str, int]:
    local = local_url.rstrip("/")
    remote = remote_url.rstrip("/")
    state = _json_request(f"{local}/api/state")
    friends = state.get("friends") if isinstance(state.get("friends"), list) else []
    cursor = read_state(state_path)

    def fetch_page(offset: int, limit: int) -> dict[str, Any]:
        return _json_request(f"{local}/api/history?offset={offset}&limit={limit}")

    events = collect_events(fetch_page, cursor)
    totals = {"friends": 0, "events": 0, "changed": 0}
    chunks = [events[index:index + 10_000] for index in range(0, len(events), 10_000)] or [[]]
    for index, chunk in enumerate(chunks):
        result = _json_request(
            f"{remote}/v1/telemetry",
            payload={
                "schema_version": 1,
                "friends": friends if index == 0 else [],
                "events": [normalize_event(event) for event in chunk],
            },
            token=token,
        )
        for key in totals:
            totals[key] += int(result.get(key, 0))
        if chunk:
            cursor = max(cursor, max(int(event["id"]) for event in chunk))
            write_state(state_path, cursor)
    return totals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-url", default=os.environ.get("PRESENCE_LOCAL_URL", "http://127.0.0.1:8842"))
    parser.add_argument("--remote-url", default=os.environ.get("PRESENCE_REMOTE_URL", ""))
    parser.add_argument("--token", default=os.environ.get("PRESENCE_COLLECTOR_TOKEN", ""))
    parser.add_argument("--token-file", default=os.environ.get("PRESENCE_COLLECTOR_TOKEN_FILE", ""))
    parser.add_argument(
        "--state",
        type=Path,
        default=Path.home() / ".presence-monitor" / "bridge-state.json",
    )
    parser.add_argument("--allow-non-loopback-local", action="store_true")
    arguments = parser.parse_args()
    validate_urls(arguments.local_url, arguments.remote_url, arguments.allow_non_loopback_local)
    token = _read_token(arguments.token, arguments.token_file)
    result = publish(arguments.local_url, arguments.remote_url, token, arguments.state)
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
