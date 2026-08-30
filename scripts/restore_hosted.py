#!/usr/bin/env python3
"""Verify a local or off-site hosted backup without replacing production data."""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

try:
    from scripts.backup_format import verify_artifact
    from scripts.r2_backup import R2BackupClient
except ModuleNotFoundError:  # Direct execution: python scripts/restore_hosted.py
    from backup_format import verify_artifact  # type: ignore[no-redef]
    from r2_backup import R2BackupClient  # type: ignore[no-redef]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--archive", type=Path)
    source.add_argument("--remote-url")
    parser.add_argument("--proxy-url", default="")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--instance-id", default="production")
    parser.add_argument("--latest-tier", choices=("hourly", "daily", "monthly"), default="hourly")
    arguments = parser.parse_args()

    if arguments.archive is not None:
        if arguments.manifest is None:
            parser.error("--manifest is required with --archive")
        report = verify_artifact(arguments.archive, arguments.manifest)
    else:
        if arguments.token_file is None:
            parser.error("--token-file is required with --remote-url")
        client = R2BackupClient(
            arguments.remote_url,
            arguments.token_file,
            proxy_url=arguments.proxy_url,
        )
        latest = client.latest(instance_id=arguments.instance_id, tier=arguments.latest_tier)
        with tempfile.TemporaryDirectory(prefix="presence-monitor-restore-cli-") as directory:
            report = client.restore_drill(str(latest["key"]), latest, Path(directory))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
