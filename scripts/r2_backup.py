#!/usr/bin/env python3
"""Replicate local backup artifacts to an authenticated Cloudflare R2 gateway."""
from __future__ import annotations

import argparse
import email.utils
import hashlib
import json
import os
import random
import re
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

try:
    from scripts.backup_format import (
        BackupArtifact,
        FORMAT,
        MAX_DATABASE_BYTES,
        manifest_path_for,
        read_manifest,
        sha256_file,
        verify_artifact,
        write_json_atomic,
    )
except ModuleNotFoundError:  # Direct execution: python scripts/r2_backup.py
    from backup_format import (  # type: ignore[no-redef]
        BackupArtifact,
        FORMAT,
        MAX_DATABASE_BYTES,
        manifest_path_for,
        read_manifest,
        sha256_file,
        verify_artifact,
        write_json_atomic,
    )


MAX_PART_BYTES = 8 * 1024 * 1024
PART_BYTES = MAX_PART_BYTES
MAX_PARTS = 10_000
MAX_GZIP_BYTES = 64 * 1024 * 1024 * 1024
MAX_CREATE_JSON_BYTES = 32 * 1024
MAX_COMPLETE_JSON_BYTES = 4 * 1024 * 1024
MAX_JSON_BYTES = 64 * 1024
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
TIERS = frozenset({"hourly", "daily", "monthly"})
STATE_FORMAT = "presence-monitor-r2-state/v1"
_INSTANCE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_ETAG_RE = re.compile(r"^[A-Za-z0-9._~:+/=-]{1,256}$")
_KEY_RE = re.compile(
    r"^backups/[a-z0-9][a-z0-9_-]{0,63}/(hourly|daily|monthly)/"
    r"[0-9]{8}T[0-9]{6}\.[0-9]{6}Z-[0-9a-f]{64}\.sqlite3\.gz$"
)
_HEADER_FIELDS = {
    "format": "X-Backup-Format",
    "created_at": "X-Backup-Created-At",
    "instance_id": "X-Backup-Instance-Id",
    "database_bytes": "X-Backup-Database-Bytes",
    "database_sha256": "X-Backup-Database-SHA256",
    "gzip_bytes": "X-Backup-Gzip-Bytes",
    "gzip_sha256": "X-Backup-Gzip-SHA256",
    "schema_version": "X-Backup-Schema-Version",
}

_MAX_PART_DESCRIPTOR_JSON_BYTES = (
    len('{"part_number":,"etag":""}') + len(str(MAX_PARTS)) + 256
)
_MAX_COMPLETE_PAYLOAD_BYTES = (
    len('{"parts":[]}')
    + (_MAX_PART_DESCRIPTOR_JSON_BYTES * MAX_PARTS)
    + (MAX_PARTS - 1)
)
if (
    MAX_GZIP_BYTES > MAX_PART_BYTES * MAX_PARTS
    or MAX_COMPLETE_JSON_BYTES < _MAX_COMPLETE_PAYLOAD_BYTES
):
    raise RuntimeError("invalid multipart capacity limits")


class PermanentBackupError(RuntimeError):
    """A backup request cannot succeed without configuration or data changes."""


class BackupTransportError(RuntimeError):
    """A retryable backup request exhausted its bounded retry budget."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def _parse_instant(value: object) -> datetime:
    try:
        instant = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise PermanentBackupError("backup timestamp is invalid") from error
    if instant.tzinfo is None:
        raise PermanentBackupError("backup timestamp must include a timezone")
    return instant.astimezone(timezone.utc)


def _instance(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _INSTANCE_RE.fullmatch(normalized):
        raise PermanentBackupError("backup instance id is invalid")
    return normalized


def _tier(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in TIERS:
        raise PermanentBackupError("backup tier is invalid")
    return normalized


def _expected_key(metadata: Mapping[str, object], instance_id: str, tier: str) -> str:
    instant = _parse_instant(metadata.get("created_at"))
    digest = str(metadata.get("gzip_sha256", ""))
    if not _DIGEST_RE.fullmatch(digest):
        raise PermanentBackupError("backup digest is invalid")
    stamp = instant.strftime("%Y%m%dT%H%M%S.%fZ")
    return f"backups/{_instance(instance_id)}/{_tier(tier)}/{stamp}-{digest}.sqlite3.gz"


def _normalize_metadata(payload: Mapping[str, object], *, key: str | None = None) -> dict[str, object]:
    if payload.get("format") != FORMAT:
        raise PermanentBackupError("backup gateway returned an unsupported format")
    instance_id = _instance(str(payload.get("instance_id", "")))
    created_at = _parse_instant(payload.get("created_at")).isoformat(timespec="microseconds")
    normalized: dict[str, object] = {
        "format": FORMAT,
        "created_at": created_at,
        "instance_id": instance_id,
    }
    for field in ("database_bytes", "gzip_bytes", "schema_version"):
        value = payload.get(field)
        if isinstance(value, bool):
            raise PermanentBackupError(f"backup {field} is invalid")
        try:
            number = int(value)  # headers are strings
        except (TypeError, ValueError) as error:
            raise PermanentBackupError(f"backup {field} is invalid") from error
        if number < 1:
            raise PermanentBackupError(f"backup {field} is invalid")
        normalized[field] = number
    if normalized["database_bytes"] > MAX_DATABASE_BYTES:
        raise PermanentBackupError("backup database_bytes is invalid")
    if normalized["gzip_bytes"] > MAX_GZIP_BYTES:
        raise PermanentBackupError("backup gzip_bytes is invalid")
    if normalized["schema_version"] != 1:
        raise PermanentBackupError("backup schema version is unsupported")
    for field in ("database_sha256", "gzip_sha256"):
        digest = str(payload.get(field, ""))
        if not _DIGEST_RE.fullmatch(digest):
            raise PermanentBackupError(f"backup {field} is invalid")
        normalized[field] = digest
    if key is not None:
        if not _KEY_RE.fullmatch(key):
            raise PermanentBackupError("backup gateway returned an invalid key")
        segments = key.split("/")
        expected = _expected_key(normalized, instance_id, segments[2])
        if key != expected:
            raise PermanentBackupError("backup key does not match its metadata")
        normalized["key"] = key
        normalized["tier"] = segments[2]
    return normalized


def _metadata_from_headers(headers: Mapping[str, str]) -> dict[str, object]:
    key = str(headers.get("X-Backup-Key", ""))
    payload = {field: headers.get(header) for field, header in _HEADER_FIELDS.items()}
    return _normalize_metadata(payload, key=key)


class R2BackupClient:
    def __init__(
        self,
        base_url: str,
        token_file: Path,
        *,
        timeout: float = 60.0,
        attempts: int = 5,
        sleeper: Callable[[float], None] = time.sleep,
        part_bytes: int = PART_BYTES,
        proxy_url: str = "",
    ):
        parsed = urllib.parse.urlsplit(str(base_url or "").rstrip("/"))
        loopback = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
            raise PermanentBackupError("backup gateway must use HTTPS")
        if not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise PermanentBackupError("backup gateway URL is invalid")
        self.base_url = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
        )
        self.token_file = Path(token_file)
        self.timeout = max(1.0, min(float(timeout), 300.0))
        self.attempts = max(1, min(int(attempts), 10))
        self.sleeper = sleeper
        self.part_bytes = max(1, min(int(part_bytes), MAX_PART_BYTES))
        proxy = self._proxy_url(proxy_url)
        proxies = {"http": proxy, "https": proxy} if proxy else {}
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler(proxies),
            _NoRedirect(),
        )

    @staticmethod
    def _proxy_url(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        parsed = urllib.parse.urlsplit(text)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise PermanentBackupError("backup proxy URL is invalid")
        try:
            port = parsed.port
        except ValueError as error:
            raise PermanentBackupError("backup proxy URL is invalid") from error
        if port is not None and not 1 <= port <= 65535:
            raise PermanentBackupError("backup proxy URL is invalid")
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        authority = f"{host}:{port}" if port is not None else host
        return urllib.parse.urlunsplit((parsed.scheme, authority, "", "", ""))

    def _token(self) -> str:
        try:
            details = self.token_file.lstat()
            if not stat.S_ISREG(details.st_mode):
                raise PermanentBackupError("backup token must be a regular file")
            if details.st_mode & 0o022:
                raise PermanentBackupError("backup token file permissions are too broad")
            token = self.token_file.read_text(encoding="utf-8").strip()
        except PermanentBackupError:
            raise
        except (OSError, UnicodeDecodeError) as error:
            raise PermanentBackupError("backup token cannot be read") from error
        if (
            not 43 <= len(token) <= 512
            or not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", token)
        ):
            raise PermanentBackupError("backup token format is invalid")
        return token

    def _url(self, path: str, query: Mapping[str, str] | None = None) -> str:
        if not path.startswith("/"):
            raise PermanentBackupError("backup request path is invalid")
        result = self.base_url + path
        if query:
            result += "?" + urllib.parse.urlencode(query, quote_via=urllib.parse.quote)
        return result

    @staticmethod
    def _retry_delay(headers: Mapping[str, str], attempt: int) -> float:
        retry_after = headers.get("Retry-After")
        if retry_after:
            try:
                return min(30.0, max(0.0, float(retry_after)))
            except ValueError:
                try:
                    instant = email.utils.parsedate_to_datetime(retry_after)
                    if instant.tzinfo is None:
                        instant = instant.replace(tzinfo=timezone.utc)
                    return min(
                        30.0,
                        max(0.0, (instant - datetime.now(timezone.utc)).total_seconds()),
                    )
                except (TypeError, ValueError, OverflowError):
                    pass
        return min(30.0, (2**attempt) + random.uniform(0.0, 0.25))

    def _execute(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        data: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        accepted: frozenset[int] = frozenset({200}),
        consumer: Callable[[Any], Any],
    ) -> Any:
        token = self._token()
        request_headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "presence-monitor-backup/1",
            **dict(headers or {}),
        }
        url = self._url(path, query)
        last_error: BaseException | None = None
        for attempt in range(self.attempts):
            request = urllib.request.Request(
                url,
                data=data,
                headers=request_headers,
                method=method,
            )
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    status = int(getattr(response, "status", response.getcode()))
                    if status not in accepted:
                        if status in RETRYABLE_STATUS:
                            raise urllib.error.HTTPError(
                                url, status, "retryable response", response.headers, response
                            )
                        raise PermanentBackupError(
                            f"backup gateway rejected request (HTTP {status})"
                        )
                    return consumer(response)
            except urllib.error.HTTPError as error:
                last_error = error
                try:
                    error.close()
                except OSError:
                    pass
                if error.code not in RETRYABLE_STATUS:
                    raise PermanentBackupError(
                        f"backup gateway rejected request (HTTP {error.code})"
                    ) from None
                headers_for_retry = error.headers or {}
            except PermanentBackupError:
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
                last_error = error
                headers_for_retry = {}
            if attempt + 1 >= self.attempts:
                break
            self.sleeper(self._retry_delay(headers_for_retry, attempt))
        raise BackupTransportError("backup gateway request failed after bounded retries") from last_error

    @staticmethod
    def _consume_json(response: Any) -> dict[str, object]:
        raw = response.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            raise PermanentBackupError("backup gateway response is too large")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
            raise PermanentBackupError("backup gateway returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise PermanentBackupError("backup gateway returned invalid JSON")
        return payload

    def _json_request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, object] | None = None,
        query: Mapping[str, str] | None = None,
        accepted: frozenset[int] = frozenset({200}),
        max_request_bytes: int = MAX_CREATE_JSON_BYTES,
    ) -> dict[str, object]:
        data = None
        headers: dict[str, str] = {}
        if payload is not None:
            data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            if len(data) > max_request_bytes:
                raise PermanentBackupError("backup gateway JSON request is too large")
            headers["Content-Type"] = "application/json"
        return self._execute(
            method,
            path,
            query=query,
            data=data,
            headers=headers,
            accepted=accepted,
            consumer=self._consume_json,
        )

    def head(self, key: str) -> dict[str, object]:
        if not _KEY_RE.fullmatch(str(key)):
            raise PermanentBackupError("backup key is invalid")
        return self._execute(
            "HEAD",
            "/v1/objects",
            query={"key": key},
            accepted=frozenset({200}),
            consumer=lambda response: _metadata_from_headers(response.headers),
        )

    @staticmethod
    def _assert_same_metadata(
        actual: Mapping[str, object], expected: Mapping[str, object], *, key: str
    ) -> None:
        normalized_expected = _normalize_metadata(expected, key=key)
        for field in (
            "format",
            "created_at",
            "instance_id",
            "database_bytes",
            "database_sha256",
            "gzip_bytes",
            "gzip_sha256",
            "schema_version",
            "key",
        ):
            if actual.get(field) != normalized_expected.get(field):
                raise PermanentBackupError("backup gateway metadata verification failed")

    def upload(
        self,
        artifact: BackupArtifact,
        *,
        instance_id: str,
        tier: str,
    ) -> dict[str, object]:
        instance_id = _instance(instance_id)
        tier = _tier(tier)
        metadata = _normalize_metadata(artifact.metadata)
        archive_bytes = artifact.archive.stat().st_size
        if archive_bytes != metadata["gzip_bytes"]:
            raise PermanentBackupError("local backup size does not match its manifest")
        expected_parts = (archive_bytes + self.part_bytes - 1) // self.part_bytes
        if expected_parts > MAX_PARTS:
            raise PermanentBackupError("backup archive requires more than 10,000 parts")
        if sha256_file(artifact.archive) != metadata["gzip_sha256"]:
            raise PermanentBackupError("local backup checksum does not match its manifest")
        expected_key = _expected_key(metadata, instance_id, tier)
        create_payload = {**metadata, "instance_id": instance_id, "tier": tier}
        created = self._json_request(
            "POST",
            "/v1/uploads",
            payload=create_payload,
            accepted=frozenset({200, 201}),
        )
        key = str(created.get("key", ""))
        if key != expected_key:
            raise PermanentBackupError("backup gateway returned an unexpected key")
        if bool(created.get("existing")):
            existing = self.head(key)
            self._assert_same_metadata(existing, create_payload, key=key)
            return existing

        upload_id = str(created.get("upload_id", ""))
        if not upload_id or len(upload_id) > 1024 or any(ord(character) < 0x20 for character in upload_id):
            raise PermanentBackupError("backup gateway returned an invalid upload id")
        encoded_upload = urllib.parse.quote(upload_id, safe="")
        parts: list[dict[str, object]] = []
        try:
            with artifact.archive.open("rb") as handle:
                part_number = 1
                while True:
                    chunk = handle.read(self.part_bytes)
                    if not chunk:
                        break
                    if part_number > MAX_PARTS:
                        raise PermanentBackupError("backup archive requires more than 10,000 parts")
                    response = self._execute(
                        "PUT",
                        f"/v1/uploads/{encoded_upload}/parts/{part_number}",
                        query={"key": key},
                        data=chunk,
                        headers={
                            "Content-Type": "application/octet-stream",
                            "Content-Length": str(len(chunk)),
                        },
                        accepted=frozenset({200}),
                        consumer=self._consume_json,
                    )
                    returned_number = response.get("part_number")
                    etag = str(response.get("etag", ""))
                    if returned_number != part_number or not _ETAG_RE.fullmatch(etag):
                        raise PermanentBackupError("backup gateway returned an invalid part receipt")
                    parts.append({"part_number": part_number, "etag": etag})
                    part_number += 1
            if not parts:
                raise PermanentBackupError("backup archive is empty")
            if len(parts) != expected_parts:
                raise PermanentBackupError("backup archive size changed during upload")
            try:
                self._json_request(
                    "POST",
                    f"/v1/uploads/{encoded_upload}/complete",
                    query={"key": key},
                    payload={"parts": parts},
                    accepted=frozenset({200, 201}),
                    max_request_bytes=MAX_COMPLETE_JSON_BYTES,
                )
            except (BackupTransportError, PermanentBackupError):
                try:
                    recovered = self.head(key)
                    self._assert_same_metadata(recovered, create_payload, key=key)
                    return recovered
                except (BackupTransportError, PermanentBackupError):
                    raise
        except Exception:
            try:
                self._execute(
                    "POST",
                    f"/v1/uploads/{encoded_upload}/abort",
                    query={"key": key},
                    accepted=frozenset({200, 204}),
                    consumer=lambda _response: {},
                )
            except (BackupTransportError, PermanentBackupError):
                pass
            raise

        stored = self.head(key)
        self._assert_same_metadata(stored, create_payload, key=key)
        return stored

    def latest(self, *, instance_id: str, tier: str = "hourly") -> dict[str, object]:
        payload = self._json_request(
            "GET",
            "/v1/latest",
            query={"instance": _instance(instance_id), "tier": _tier(tier)},
        )
        key = str(payload.get("key", ""))
        return _normalize_metadata(payload, key=key)

    def download(self, key: str, destination: Path) -> Path:
        if not _KEY_RE.fullmatch(str(key)):
            raise PermanentBackupError("backup key is invalid")
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if destination.exists():
            raise PermanentBackupError("restore destination already exists")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", dir=destination.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)

        def consume(response: Any) -> dict[str, object]:
            metadata = _metadata_from_headers(response.headers)
            if metadata["key"] != key:
                raise PermanentBackupError("backup gateway returned an unexpected object")
            digest = hashlib.sha256()
            written = 0
            with temporary.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > int(metadata["gzip_bytes"]):
                        raise PermanentBackupError("download exceeds advertised backup size")
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if written != metadata["gzip_bytes"] or digest.hexdigest() != metadata["gzip_sha256"]:
                raise PermanentBackupError("downloaded backup failed checksum verification")
            return metadata

        try:
            self._execute(
                "GET",
                "/v1/objects",
                query={"key": key},
                accepted=frozenset({200}),
                consumer=consume,
            )
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
            return destination
        finally:
            temporary.unlink(missing_ok=True)

    def restore_drill(
        self,
        key: str,
        metadata: Mapping[str, object],
        state_dir: Path,
    ) -> dict[str, object]:
        expected = _normalize_metadata(metadata, key=key)
        state_dir = state_dir.resolve()
        state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(prefix="r2-restore-drill-", dir=state_dir) as directory:
            root = Path(directory)
            archive = root / "drill.sqlite3.gz"
            self.download(key, archive)
            manifest = manifest_path_for(archive)
            write_json_atomic(
                manifest,
                {field: expected[field] for field in _HEADER_FIELDS},
            )
            report = verify_artifact(archive, manifest, work_dir=root)
        completed_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        write_json_atomic(
            state_dir / ".last-restore-drill.json",
            {
                "completed_at": completed_at,
                "key": key,
                "gzip_sha256": expected["gzip_sha256"],
                "database_sha256": expected["database_sha256"],
                "integrity": report["integrity"],
                "table_counts": report["table_counts"],
            },
        )
        return report


def _artifact_pairs(backup_dir: Path) -> list[BackupArtifact]:
    artifacts: list[BackupArtifact] = []
    for manifest in sorted(backup_dir.glob("presence-monitor-*.manifest.json")):
        archive = manifest.with_name(
            manifest.name[: -len(".manifest.json")] + ".sqlite3.gz"
        )
        if not archive.is_file():
            continue
        metadata = read_manifest(manifest)
        artifacts.append(BackupArtifact(archive=archive, manifest=manifest, metadata=metadata))
    artifacts.sort(key=lambda item: str(item.metadata["created_at"]))
    return artifacts


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"format": STATE_FORMAT, "hourly": {}, "daily": {}, "monthly": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise PermanentBackupError("off-site backup state is invalid") from error
    if not isinstance(payload, dict) or payload.get("format") != STATE_FORMAT:
        raise PermanentBackupError("off-site backup state is invalid")
    for tier in TIERS:
        if not isinstance(payload.get(tier), dict):
            raise PermanentBackupError("off-site backup state is invalid")
    return payload


def sync_once(
    backup_dir: Path,
    state_dir: Path,
    client: R2BackupClient,
    instance_id: str,
) -> dict[str, object]:
    backup_dir = backup_dir.resolve()
    state_dir = state_dir.resolve()
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    artifacts = _artifact_pairs(backup_dir)
    if not artifacts:
        raise PermanentBackupError("no local backup artifacts are available")
    state_path = state_dir / ".offsite-state.json"
    state = _load_state(state_path)
    uploaded = 0
    freshly_uploaded: set[tuple[str, str]] = set()

    def upload_for(artifact: BackupArtifact, tier: str, marker: str) -> dict[str, object]:
        nonlocal uploaded
        tier_state = state[tier]
        if marker in tier_state:
            return {
                "key": tier_state[marker]["key"],
                **artifact.metadata,
                "instance_id": _instance(instance_id),
                "tier": tier,
            }
        result = client.upload(artifact, instance_id=instance_id, tier=tier)
        tier_state[marker] = {
            "key": result["key"],
            "gzip_sha256": result["gzip_sha256"],
            "completed_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        }
        state["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        write_json_atomic(state_path, state)
        uploaded += 1
        freshly_uploaded.add((tier, marker))
        return result

    hourly_result: dict[str, object] | None = None
    for artifact in artifacts:
        hourly_result = upload_for(
            artifact,
            "hourly",
            str(artifact.metadata["gzip_sha256"]),
        )

    latest_by_day: dict[str, BackupArtifact] = {}
    latest_by_month: dict[str, BackupArtifact] = {}
    for artifact in artifacts:
        instant = _parse_instant(artifact.metadata["created_at"])
        latest_by_day[instant.date().isoformat()] = artifact
        latest_by_month[instant.strftime("%Y-%m")] = artifact
    for day, artifact in sorted(latest_by_day.items()):
        upload_for(artifact, "daily", day)
    for month, artifact in sorted(latest_by_month.items()):
        upload_for(artifact, "monthly", month)

    assert hourly_result is not None
    latest_digest = str(artifacts[-1].metadata["gzip_sha256"])
    success_marker = state_dir / ".last-offsite-success.json"
    marker_is_recent = _marker_recent(
        success_marker,
        7200,
        datetime.now(timezone.utc),
    )
    if ("hourly", latest_digest) in freshly_uploaded or not marker_is_recent:
        if ("hourly", latest_digest) not in freshly_uploaded:
            verified = client.head(str(hourly_result["key"]))
            client._assert_same_metadata(
                verified,
                {**artifacts[-1].metadata, "instance_id": _instance(instance_id)},
                key=str(hourly_result["key"]),
            )
            hourly_result = verified
        completed_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        write_json_atomic(
            success_marker,
            {
                "completed_at": completed_at,
                "key": hourly_result["key"],
                "gzip_sha256": hourly_result["gzip_sha256"],
                "database_sha256": hourly_result["database_sha256"],
            },
        )
    return {
        "uploaded": uploaded,
        "key": hourly_result["key"],
        "gzip_sha256": hourly_result["gzip_sha256"],
        "database_sha256": hourly_result["database_sha256"],
    }


def _marker_recent(path: Path, max_age_seconds: int, now: datetime) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        completed_at = _parse_instant(payload["completed_at"])
    except (OSError, KeyError, TypeError, json.JSONDecodeError, PermanentBackupError):
        return False
    age = (now - completed_at).total_seconds()
    return -300 <= age <= max_age_seconds


def offsite_backup_healthy(
    state_dir: Path,
    *,
    max_upload_age_seconds: int,
    max_drill_age_seconds: int,
    now: datetime | None = None,
) -> bool:
    if max_upload_age_seconds < 1 or max_drill_age_seconds < 1:
        return False
    instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    root = state_dir.resolve()
    return _marker_recent(
        root / ".last-offsite-success.json", max_upload_age_seconds, instant
    ) and _marker_recent(root / ".last-restore-drill.json", max_drill_age_seconds, instant)


def _drill_due(state_dir: Path, interval_seconds: int) -> bool:
    return not _marker_recent(
        state_dir.resolve() / ".last-restore-drill.json",
        max(1, interval_seconds),
        datetime.now(timezone.utc),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--remote-url")
    parser.add_argument("--proxy-url", default="")
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--instance-id", default="production")
    parser.add_argument("--interval-seconds", type=int, default=0)
    parser.add_argument("--drill-interval-seconds", type=int, default=86400)
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--max-upload-age-seconds", type=int, default=7200)
    parser.add_argument("--max-drill-age-seconds", type=int, default=93600)
    arguments = parser.parse_args()
    if arguments.health:
        return 0 if offsite_backup_healthy(
            arguments.state_dir,
            max_upload_age_seconds=arguments.max_upload_age_seconds,
            max_drill_age_seconds=arguments.max_drill_age_seconds,
        ) else 1
    if not arguments.remote_url or arguments.token_file is None:
        parser.error("--remote-url and --token-file are required unless --health is used")

    client = R2BackupClient(
        arguments.remote_url,
        arguments.token_file,
        proxy_url=arguments.proxy_url,
    )
    interval = max(0, arguments.interval_seconds)
    drill_interval = max(1, arguments.drill_interval_seconds)
    while True:
        try:
            result = sync_once(
                arguments.backup_dir,
                arguments.state_dir,
                client,
                arguments.instance_id,
            )
            if _drill_due(arguments.state_dir, drill_interval):
                latest = client.latest(instance_id=arguments.instance_id, tier="hourly")
                client.restore_drill(str(latest["key"]), latest, arguments.state_dir)
            print(json.dumps(result, sort_keys=True, separators=(",", ":")), flush=True)
        except (OSError, PermanentBackupError, BackupTransportError) as error:
            print(
                f"off-site backup failed: {type(error).__name__}: {error}",
                file=sys.stderr,
                flush=True,
            )
            if not interval:
                return 1
        if not interval:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
