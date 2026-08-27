#!/usr/bin/env python3
"""Publish normalized local snapshots to a Hosted instance without sharing VRChat credentials."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
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
EVENT_CHUNK_SIZE = 1_000
MAX_LOCAL_EXPORT_BYTES = 64 * 1024 * 1024
LEGACY_EVENT_FIELDS = (
    "friend_id",
    "occurred_at",
    "old_status",
    "new_status",
    "location",
    "platform",
    "source",
)


class BridgeHTTPError(RuntimeError):
    """An HTTP response that exhausted the bridge retry policy."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = int(status_code)
        super().__init__(f"HTTP {self.status_code}: {detail}")


def _request_bytes(
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    token: str = "",
    attempts: int = 4,
    accept: str = "application/json",
    max_bytes: int = 4 * 1024 * 1024,
) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode() if payload is not None else None
    headers = {"Accept": accept, "User-Agent": "PresenceMonitorBridge/1"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")

    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = response.read(max_bytes + 1)
                if len(result) > max_bytes:
                    raise RuntimeError("服务响应超过大小限制")
                return result
        except urllib.error.HTTPError as error:
            try:
                retryable = error.code == 429 or 500 <= error.code < 600
                if not retryable or attempt + 1 >= attempts:
                    detail = error.read(2048).decode("utf-8", "replace")
                    raise BridgeHTTPError(error.code, detail or str(error.reason)) from error
                retry_after = error.headers.get("Retry-After", "")
                try:
                    delay = max(1.0, min(float(retry_after), 300.0))
                except ValueError:
                    delay = min(30.0, 2.0**attempt + random.random())
            finally:
                error.close()
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as error:
            if attempt + 1 >= attempts:
                raise RuntimeError(f"网络请求失败：{error}") from error
            time.sleep(min(30.0, 2.0**attempt + random.random()))
    raise RuntimeError("网络请求失败")


def _json_request(
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    token: str = "",
    attempts: int = 4,
) -> dict[str, Any]:
    raw = _request_bytes(url, payload=payload, token=token, attempts=attempts)
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise RuntimeError("服务返回了无效 JSON") from error
    if not isinstance(result, dict):
        raise RuntimeError("服务返回的 JSON 不是对象")
    return result


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
    supplied_id = str(event.get("client_event_id") or "").strip()
    client_event_id = supplied_id or f"local-{int(event['id'])}"
    return {
        "client_event_id": client_event_id,
        "friend_id": str(event.get("friend_id") or ""),
        "occurred_at": str(event.get("occurred_at") or ""),
        "old_status": str(event.get("old_status") or "unknown"),
        "new_status": str(event.get("new_status") or "offline"),
        "location": str(event.get("location") or ""),
        "platform": str(event.get("platform") or ""),
        "source": str(event.get("source") or "local-bridge"),
    }


def collect_legacy_csv(raw: bytes) -> list[dict[str, Any]]:
    """Read the complete append-only CSV exposed by pre-pagination local servers."""

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise RuntimeError("本机历史 CSV 编码无效") from error
    reader = csv.DictReader(io.StringIO(text, newline=""))
    fieldnames = set(reader.fieldnames or [])
    required = {"friend_id", "occurred_at", "new_status"}
    if not required <= fieldnames:
        raise RuntimeError("本机历史 CSV 缺少必需列")

    events: list[dict[str, Any]] = []
    occurrences: dict[str, int] = {}
    for row in reader:
        event = {field: str(row.get(field) or "") for field in LEGACY_EVENT_FIELDS}
        if not event["friend_id"] or not event["occurred_at"]:
            continue
        event["old_status"] = event["old_status"] or "unknown"
        event["new_status"] = event["new_status"] or "offline"
        event["source"] = event["source"] or "legacy-csv"
        canonical = json.dumps(
            event, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        fingerprint = hashlib.sha256(canonical).hexdigest()
        occurrence = occurrences.get(fingerprint, 0)
        occurrences[fingerprint] = occurrence + 1
        event["client_event_id"] = f"legacy-csv-{fingerprint}-{occurrence}"
        events.append(event)
    return events


def legacy_prefix_digest(events: list[dict[str, Any]], count: int | None = None) -> str:
    digest = hashlib.sha256()
    selected = events if count is None else events[: max(0, count)]
    for event in selected:
        encoded = str(event["client_event_id"]).encode("ascii")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def read_bridge_state(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {}
        mode = str(payload.get("mode") or "paged")
        return {
            "mode": mode if mode in {"paged", "legacy-csv"} else "paged",
            "last_event_id": max(0, int(payload.get("last_event_id", 0))),
            "legacy_count": max(0, int(payload.get("legacy_count", 0))),
            "legacy_prefix_sha256": str(payload.get("legacy_prefix_sha256") or ""),
        }
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def read_state(path: Path) -> int:
    return int(read_bridge_state(path).get("last_event_id", 0))


def write_state(
    path: Path,
    last_event_id: int,
    *,
    mode: str = "paged",
    legacy_count: int = 0,
    legacy_prefix_sha256: str = "",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".bridge-state-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "version": 2,
                    "mode": mode,
                    "last_event_id": max(0, int(last_event_id)),
                    "legacy_count": max(0, int(legacy_count)),
                    "legacy_prefix_sha256": str(legacy_prefix_sha256),
                },
                handle,
                separators=(",", ":"),
            )
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
    bridge_state = read_bridge_state(state_path)
    cursor = int(bridge_state.get("last_event_id", 0))

    def fetch_page(offset: int, limit: int) -> dict[str, Any]:
        return _json_request(f"{local}/api/history?offset={offset}&limit={limit}")

    legacy_mode = bridge_state.get("mode") == "legacy-csv"
    legacy_events: list[dict[str, Any]] = []
    if not legacy_mode:
        try:
            events = collect_events(fetch_page, cursor)
        except BridgeHTTPError as error:
            if error.status_code != 404:
                raise
            legacy_mode = True
    if legacy_mode:
        raw_csv = _request_bytes(
            f"{local}/api/export.csv",
            accept="text/csv",
            max_bytes=MAX_LOCAL_EXPORT_BYTES,
        )
        legacy_events = collect_legacy_csv(raw_csv)
        previous_count = int(bridge_state.get("legacy_count", 0))
        previous_digest = str(bridge_state.get("legacy_prefix_sha256") or "")
        prefix_is_unchanged = (
            previous_count <= len(legacy_events)
            and bool(previous_digest)
            and legacy_prefix_digest(legacy_events, previous_count) == previous_digest
        )
        events = legacy_events[previous_count:] if prefix_is_unchanged else legacy_events

    totals = {"friends": 0, "events": 0, "changed": 0}
    chunks = [
        events[index : index + EVENT_CHUNK_SIZE]
        for index in range(0, len(events), EVENT_CHUNK_SIZE)
    ] or [[]]
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
        if chunk and not legacy_mode:
            cursor = max(cursor, max(int(event["id"]) for event in chunk))
            write_state(state_path, cursor)
    if legacy_mode:
        write_state(
            state_path,
            cursor,
            mode="legacy-csv",
            legacy_count=len(legacy_events),
            legacy_prefix_sha256=legacy_prefix_digest(legacy_events),
        )
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
