#!/usr/bin/env python3
"""Create consistent compressed SQLite backups without stopping the API."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts.backup_format import (
        BackupValidationError,
        create_artifact,
        manifest_path_for,
        verify_artifact,
        write_json_atomic,
    )
except ModuleNotFoundError:  # Direct execution: python scripts/backup_hosted.py
    from backup_format import (  # type: ignore[no-redef]
        BackupValidationError,
        create_artifact,
        manifest_path_for,
        verify_artifact,
        write_json_atomic,
    )


def backup(database: Path, output: Path, keep: int, *, instance_id: str = "production") -> Path:
    destination_dir = output.resolve()
    destination_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(destination_dir, 0o700)

    lock_path = destination_dir / ".backup.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another backup is already running") from error

        artifact = create_artifact(database, destination_dir, instance_id=instance_id)
        report = verify_artifact(artifact.archive, artifact.manifest)
        completed_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        write_json_atomic(
            destination_dir / ".last-local-success.json",
            {
                "completed_at": completed_at,
                "archive": artifact.archive.name,
                "manifest": artifact.manifest.name,
                "gzip_bytes": artifact.metadata["gzip_bytes"],
                "gzip_sha256": artifact.metadata["gzip_sha256"],
                "database_sha256": artifact.metadata["database_sha256"],
                "integrity": report["integrity"],
            },
        )

        backups = sorted(destination_dir.glob("presence-monitor-*.sqlite3.gz"), reverse=True)
        for stale in backups[max(1, keep):]:
            stale.unlink(missing_ok=True)
            manifest_path_for(stale).unlink(missing_ok=True)
    return artifact.archive


def local_backup_healthy(output: Path, *, max_age_seconds: int, now: datetime | None = None) -> bool:
    marker = output.resolve() / ".last-local-success.json"
    if max_age_seconds < 1:
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        completed_at = datetime.fromisoformat(str(payload["completed_at"]).replace("Z", "+00:00"))
        if completed_at.tzinfo is None:
            return False
        instant = now or datetime.now(timezone.utc)
        age = (instant.astimezone(timezone.utc) - completed_at.astimezone(timezone.utc)).total_seconds()
        return -300 <= age <= max_age_seconds
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--keep", type=int, default=48)
    parser.add_argument("--instance-id", default="production")
    parser.add_argument("--interval-seconds", type=int, default=0)
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--max-age-seconds", type=int, default=7200)
    arguments = parser.parse_args()
    if arguments.health:
        return 0 if local_backup_healthy(
            arguments.output, max_age_seconds=arguments.max_age_seconds
        ) else 1
    if arguments.database is None:
        parser.error("--database is required unless --health is used")
    interval = max(0, arguments.interval_seconds)
    while True:
        try:
            created = backup(
                arguments.database,
                arguments.output,
                max(1, arguments.keep),
                instance_id=arguments.instance_id,
            )
            print(created, flush=True)
        except (OSError, RuntimeError, ValueError, BackupValidationError) as error:
            print(f"backup failed: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
            if not interval:
                return 1
        if not interval:
            break
        time.sleep(interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
