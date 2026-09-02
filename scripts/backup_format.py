#!/usr/bin/env python3
"""Create and verify portable Presence Monitor SQLite backup artifacts."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FORMAT = "presence-monitor-sqlite-backup/v1"
SCHEMA_VERSION = 1
MANIFEST_LIMIT_BYTES = 64 * 1024
MAX_DATABASE_BYTES = 64 * 1024 * 1024 * 1024
REQUIRED_TABLES = frozenset(
    {
        "tenants",
        "collectors",
        "viewer_tokens",
        "login_codes",
        "friends",
        "status_events",
    }
)
COUNT_TABLES = (
    "tenants",
    "collectors",
    "viewer_tokens",
    "login_codes",
    "friends",
    "status_events",
    "raw_fetches",
    "collection_samples",
    "friend_annotations",
    "tags",
    "friend_tags",
    "friend_tracking_events",
    "friend_identity_events",
    "event_anomalies",
    "tenant_preferences",
    "dashboard_configs",
    "world_cache",
    "world_resolution_state",
)
_INSTANCE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class BackupValidationError(RuntimeError):
    """Raised when a backup cannot be proven safe to restore."""


@dataclass(frozen=True)
class BackupArtifact:
    archive: Path
    manifest: Path
    metadata: dict[str, object]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_path_for(archive: Path) -> Path:
    suffix = ".sqlite3.gz"
    if not archive.name.endswith(suffix):
        raise ValueError("backup archive must end with .sqlite3.gz")
    return archive.with_name(archive.name[: -len(suffix)] + ".manifest.json")


def write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _utc(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _validate_instance_id(instance_id: str) -> str:
    normalized = str(instance_id or "").strip().lower()
    if not _INSTANCE_RE.fullmatch(normalized):
        raise ValueError("instance_id must contain only lowercase letters, digits, _ or -")
    return normalized


def _sqlite_uri(path: Path, *, immutable: bool = False) -> str:
    suffix = "?mode=ro&immutable=1" if immutable else "?mode=ro"
    return path.resolve().as_uri() + suffix


def _inspect_sqlite(path: Path) -> tuple[set[str], str]:
    try:
        with closing(
            sqlite3.connect(_sqlite_uri(path, immutable=True), uri=True, timeout=30)
        ) as connection:
            connection.execute("PRAGMA query_only=ON")
            row = connection.execute("PRAGMA integrity_check").fetchone()
            integrity = str(row[0]) if row else "missing result"
            tables = {
                str(item[0])
                for item in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
    except sqlite3.Error as error:
        raise BackupValidationError("backup is not a readable SQLite database") from error
    if integrity != "ok":
        raise BackupValidationError("backup integrity check failed")
    missing = REQUIRED_TABLES - tables
    if missing:
        raise BackupValidationError("backup schema is incomplete")
    return tables, integrity


def create_artifact(
    database: Path,
    output: Path,
    *,
    instance_id: str,
    created_at: datetime | None = None,
) -> BackupArtifact:
    """Create a consistent gzip archive and manifest from a live SQLite file."""

    source = database.resolve(strict=True)
    if not source.is_file():
        raise ValueError("database must be a regular file")
    normalized_instance = _validate_instance_id(instance_id)
    destination_dir = output.resolve()
    destination_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(destination_dir, 0o700)
    instant = _utc(created_at)
    stamp = instant.strftime("%Y%m%dT%H%M%S.%fZ")

    database_descriptor, database_name = tempfile.mkstemp(
        prefix=".presence-monitor-snapshot-", suffix=".sqlite3", dir=destination_dir
    )
    os.close(database_descriptor)
    snapshot = Path(database_name)
    archive_temporary: Path | None = None
    archive: Path | None = None
    manifest: Path | None = None
    try:
        try:
            with closing(
                sqlite3.connect(_sqlite_uri(source), uri=True, timeout=30)
            ) as source_db:
                source_db.execute("PRAGMA query_only=ON")
                with closing(sqlite3.connect(snapshot, timeout=30)) as destination_db:
                    source_db.backup(destination_db)
                    destination_db.execute("PRAGMA optimize")
                    destination_db.commit()
                    journal_mode = destination_db.execute(
                        "PRAGMA journal_mode=DELETE"
                    ).fetchone()
                    if not journal_mode or str(journal_mode[0]).lower() != "delete":
                        raise BackupValidationError(
                            "backup snapshot could not be finalized"
                        )
        except sqlite3.Error as error:
            raise BackupValidationError("SQLite online backup failed") from error

        _inspect_sqlite(snapshot)
        os.chmod(snapshot, 0o600)
        database_bytes = snapshot.stat().st_size
        if database_bytes <= 0 or database_bytes > MAX_DATABASE_BYTES:
            raise BackupValidationError("backup database size is outside the supported range")
        database_sha256 = sha256_file(snapshot)

        archive_descriptor, archive_name = tempfile.mkstemp(
            prefix=".presence-monitor-archive-", suffix=".sqlite3.gz", dir=destination_dir
        )
        archive_temporary = Path(archive_name)
        with os.fdopen(archive_descriptor, "wb") as compressed_file:
            with snapshot.open("rb") as source_file:
                with gzip.GzipFile(
                    filename="",
                    mode="wb",
                    compresslevel=9,
                    fileobj=compressed_file,
                    mtime=0,
                ) as gzip_file:
                    shutil.copyfileobj(source_file, gzip_file, length=1024 * 1024)
            compressed_file.flush()
            os.fsync(compressed_file.fileno())
        os.chmod(archive_temporary, 0o600)

        gzip_bytes = archive_temporary.stat().st_size
        if gzip_bytes <= 0:
            raise BackupValidationError("compressed backup is empty")
        gzip_sha256 = sha256_file(archive_temporary)
        base_name = f"presence-monitor-{stamp}-{gzip_sha256[:16]}"
        archive = destination_dir / f"{base_name}.sqlite3.gz"
        manifest = destination_dir / f"{base_name}.manifest.json"
        if archive.exists() or manifest.exists():
            raise FileExistsError("backup artifact already exists")

        metadata: dict[str, object] = {
            "format": FORMAT,
            "created_at": instant.isoformat(timespec="microseconds"),
            "instance_id": normalized_instance,
            "database_bytes": database_bytes,
            "database_sha256": database_sha256,
            "gzip_bytes": gzip_bytes,
            "gzip_sha256": gzip_sha256,
            "schema_version": SCHEMA_VERSION,
        }
        os.replace(archive_temporary, archive)
        archive_temporary = None
        _fsync_directory(destination_dir)
        try:
            write_json_atomic(manifest, metadata)
        except Exception:
            archive.unlink(missing_ok=True)
            _fsync_directory(destination_dir)
            raise
        return BackupArtifact(archive=archive, manifest=manifest, metadata=metadata)
    finally:
        for temporary in (
            snapshot,
            snapshot.with_name(f"{snapshot.name}-wal"),
            snapshot.with_name(f"{snapshot.name}-shm"),
        ):
            temporary.unlink(missing_ok=True)
        if archive_temporary is not None:
            archive_temporary.unlink(missing_ok=True)


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise BackupValidationError("backup manifest is missing") from error
    if size <= 0 or size > MANIFEST_LIMIT_BYTES:
        raise BackupValidationError("backup manifest size is invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise BackupValidationError("backup manifest is invalid") from error
    if not isinstance(payload, dict):
        raise BackupValidationError("backup manifest is invalid")
    return payload


def _validate_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("format") != FORMAT:
        raise BackupValidationError("unsupported backup format")
    try:
        created_at = datetime.fromisoformat(str(payload["created_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError, TypeError) as error:
        raise BackupValidationError("backup timestamp is invalid") from error
    if created_at.tzinfo is None:
        raise BackupValidationError("backup timestamp must include a timezone")
    try:
        instance_id = _validate_instance_id(str(payload["instance_id"]))
    except (KeyError, ValueError) as error:
        raise BackupValidationError("backup instance is invalid") from error

    validated: dict[str, Any] = dict(payload)
    validated["created_at"] = created_at.astimezone(timezone.utc).isoformat(timespec="microseconds")
    validated["instance_id"] = instance_id
    for field in ("database_bytes", "gzip_bytes", "schema_version"):
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise BackupValidationError(f"backup {field} is invalid")
    if int(payload["database_bytes"]) > MAX_DATABASE_BYTES:
        raise BackupValidationError("backup database is too large")
    if int(payload["schema_version"]) != SCHEMA_VERSION:
        raise BackupValidationError("unsupported backup schema version")
    for field in ("database_sha256", "gzip_sha256"):
        if not _DIGEST_RE.fullmatch(str(payload.get(field, ""))):
            raise BackupValidationError(f"backup {field} is invalid")
    return validated


def read_manifest(path: Path) -> dict[str, Any]:
    """Load and validate a bounded backup manifest."""

    return _validate_manifest(_load_manifest(path))


def verify_artifact(
    archive: Path,
    manifest: Path,
    *,
    work_dir: Path | None = None,
) -> dict[str, object]:
    """Verify both digests and prove the decompressed database is usable."""

    payload = read_manifest(manifest)
    try:
        archive_bytes = archive.stat().st_size
    except OSError as error:
        raise BackupValidationError("backup archive is missing") from error
    if archive_bytes != payload["gzip_bytes"]:
        raise BackupValidationError("compressed size does not match the manifest")
    if sha256_file(archive) != payload["gzip_sha256"]:
        raise BackupValidationError("compressed checksum does not match the manifest")

    temporary_parent = work_dir.resolve() if work_dir is not None else None
    if temporary_parent is not None:
        temporary_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with tempfile.TemporaryDirectory(prefix="presence-monitor-restore-", dir=temporary_parent) as directory:
            restored = Path(directory) / "restored.sqlite3"
            written = 0
            digest = hashlib.sha256()
            try:
                with gzip.open(archive, "rb") as source, restored.open("xb") as destination:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > int(payload["database_bytes"]):
                            raise BackupValidationError("decompressed backup exceeds the manifest size")
                        digest.update(chunk)
                        destination.write(chunk)
                    destination.flush()
                    os.fsync(destination.fileno())
            except (OSError, EOFError) as error:
                raise BackupValidationError("backup gzip stream is invalid") from error
            if written != payload["database_bytes"]:
                raise BackupValidationError("database size does not match the manifest")
            if digest.hexdigest() != payload["database_sha256"]:
                raise BackupValidationError("database checksum does not match the manifest")
            tables, integrity = _inspect_sqlite(restored)
            counts: dict[str, int] = {}
            with closing(
                sqlite3.connect(_sqlite_uri(restored, immutable=True), uri=True, timeout=30)
            ) as connection:
                connection.execute("PRAGMA query_only=ON")
                for table in COUNT_TABLES:
                    if table in tables:
                        counts[table] = int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    except BackupValidationError:
        raise
    except OSError as error:
        raise BackupValidationError("backup restore verification failed") from error

    return {
        "format": payload["format"],
        "created_at": payload["created_at"],
        "instance_id": payload["instance_id"],
        "schema_version": payload["schema_version"],
        "database_bytes": payload["database_bytes"],
        "database_sha256": payload["database_sha256"],
        "gzip_bytes": payload["gzip_bytes"],
        "gzip_sha256": payload["gzip_sha256"],
        "integrity": integrity,
        "table_counts": counts,
    }
