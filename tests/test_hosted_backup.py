from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.backup_format import BackupValidationError, create_artifact, verify_artifact
from scripts.backup_hosted import backup, local_backup_healthy
from server.storage import Store


class HostedBackupTests(unittest.TestCase):
    @staticmethod
    def _database(path: Path) -> None:
        store = Store(str(path))
        credentials = store.bootstrap("测试空间", "测试采集器")
        store.ingest(
            credentials["tenant_id"],
            credentials["collector_id"],
            [
                {
                    "id": "usr_test",
                    "username": "tester",
                    "displayName": "Tester",
                    "status": "active",
                    "location": "wrld_test:1",
                    "updatedAt": "2026-08-27T12:00:00+00:00",
                }
            ],
            [
                {
                    "id": "event-1",
                    "friend_id": "usr_test",
                    "occurred_at": "2026-08-27T12:00:00+00:00",
                    "old_status": "offline",
                    "new_status": "active",
                }
            ],
        )

    def test_artifact_is_deterministic_checksummed_and_restorable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "hosted.sqlite3"
            output = root / "backups"
            self._database(database)

            created_at = datetime(2026, 8, 27, 12, 30, tzinfo=timezone.utc)
            artifact = create_artifact(database, output, instance_id="test", created_at=created_at)
            report = verify_artifact(artifact.archive, artifact.manifest)

            self.assertEqual(report["integrity"], "ok")
            self.assertEqual(report["table_counts"]["friends"], 1)
            self.assertEqual(report["table_counts"]["status_events"], 1)
            self.assertEqual(report["format"], "presence-monitor-sqlite-backup/v1")
            self.assertEqual(artifact.metadata["created_at"], "2026-08-27T12:30:00.000000+00:00")
            self.assertIn(str(artifact.metadata["gzip_sha256"])[:16], artifact.archive.name)
            self.assertEqual(artifact.archive.stat().st_mode & 0o777, 0o600)
            self.assertEqual(artifact.manifest.stat().st_mode & 0o777, 0o600)
            self.assertFalse(list(output.glob("*.sqlite3")))
            self.assertFalse(list(output.glob(".presence-monitor-*")))

    def test_corrupt_archive_is_rejected_before_sqlite_is_opened(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "hosted.sqlite3"
            self._database(database)
            artifact = create_artifact(database, root / "backups", instance_id="test")

            damaged = bytearray(artifact.archive.read_bytes())
            damaged[len(damaged) // 2] ^= 1
            artifact.archive.write_bytes(damaged)

            with self.assertRaisesRegex(BackupValidationError, "compressed checksum"):
                verify_artifact(artifact.archive, artifact.manifest)

    def test_backup_is_consistent_and_rotates_archive_manifest_pairs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "hosted.sqlite3"
            output = root / "backups"
            self._database(database)

            first = backup(database, output, keep=1)
            second = backup(database, output, keep=1)

            self.assertTrue(second.is_file())
            manifests = list(output.glob("presence-monitor-*.manifest.json"))
            self.assertEqual(len(list(output.glob("presence-monitor-*.sqlite3.gz"))), 1)
            self.assertEqual(len(manifests), 1)
            self.assertEqual(verify_artifact(second, manifests[0])["integrity"], "ok")
            self.assertNotEqual(first.name, second.name)
            self.assertFalse(first.exists())
            self.assertTrue((output / ".last-local-success.json").is_file())
            self.assertTrue(local_backup_healthy(output, max_age_seconds=120))

    def test_local_health_rejects_stale_or_malformed_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            marker = output / ".last-local-success.json"
            marker.write_text("not json", encoding="utf-8")
            self.assertFalse(local_backup_healthy(output, max_age_seconds=120))

            stale = datetime.now(timezone.utc) - timedelta(hours=3)
            marker.write_text(json.dumps({"completed_at": stale.isoformat()}), encoding="utf-8")
            self.assertFalse(local_backup_healthy(output, max_age_seconds=120))

    def test_online_backup_does_not_include_an_uncommitted_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "hosted.sqlite3"
            self._database(database)

            writer = sqlite3.connect(database, timeout=5)
            try:
                writer.execute("BEGIN IMMEDIATE")
                writer.execute(
                    "UPDATE friends SET display_name='Uncommitted' WHERE id='usr_test'"
                )
                artifact = create_artifact(database, root / "backups", instance_id="test")
            finally:
                writer.rollback()
                writer.close()

            report = verify_artifact(artifact.archive, artifact.manifest)
            self.assertEqual(report["table_counts"]["friends"], 1)

    def test_container_contract_has_independent_local_and_offsite_backup_health(self):
        project = Path(__file__).resolve().parents[1]
        compose = (project / "docker-compose.yml").read_text(encoding="utf-8")
        dockerfile = (project / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("  offsite-backup:", compose)
        self.assertIn('profiles: ["offsite"]', compose)
        self.assertIn("/run/secrets/backup_token", compose)
        self.assertIn("--max-upload-age-seconds", compose)
        self.assertIn("--max-drill-age-seconds", compose)
        self.assertIn('"3600"', compose)
        self.assertIn("scripts/backup_format.py", dockerfile)
        self.assertIn("scripts/r2_backup.py", dockerfile)
        self.assertIn("scripts/restore_hosted.py", dockerfile)
        self.assertIn(
            "ARG NPM_CONFIG_REGISTRY=https://registry.npmjs.org", dockerfile
        )
        self.assertIn(
            "cloudflare/cloudflared:2026.8.2@sha256:"
            "0aa26e284f05e6c77ae375b8c9c11d9eb6a448fb7bcd8d40f31cb6176189eb38",
            compose,
        )

        deployment = (project / "docs/deployment.md").read_text(encoding="utf-8")
        self.assertIn("token files to owner UID `10001`", deployment)
        self.assertIn("tunnel token to owner UID `65532`", deployment)
        self.assertIn("operator's primary GID and mode `0440`", deployment)

        offsite = compose.split("  offsite-backup:", 1)[1].split("\nvolumes:", 1)[0]
        self.assertNotIn("/data", offsite)
        self.assertNotIn("BACKUP_INSTANCE_ID", offsite)
        self.assertIn("- production", offsite)


if __name__ == "__main__":
    unittest.main()
