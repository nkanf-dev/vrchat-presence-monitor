from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from scripts.backup_format import BackupArtifact, create_artifact
from scripts.r2_backup import (
    MAX_COMPLETE_JSON_BYTES,
    MAX_GZIP_BYTES,
    MAX_PART_BYTES,
    MAX_PARTS,
    PermanentBackupError,
    R2BackupClient,
    offsite_backup_healthy,
    sync_once,
)
from server.storage import Store


TOKEN = "test-token-" + "a" * 52
HEADER_FIELDS = {
    "format": "X-Backup-Format",
    "created_at": "X-Backup-Created-At",
    "instance_id": "X-Backup-Instance-Id",
    "database_bytes": "X-Backup-Database-Bytes",
    "database_sha256": "X-Backup-Database-SHA256",
    "gzip_bytes": "X-Backup-Gzip-Bytes",
    "gzip_sha256": "X-Backup-Gzip-SHA256",
    "schema_version": "X-Backup-Schema-Version",
}


class GatewayState:
    def __init__(self):
        self.uploads: dict[str, dict[str, object]] = {}
        self.objects: dict[str, tuple[bytes, dict[str, object]]] = {}
        self.parts: list[tuple[int, int]] = []
        self.requests: list[tuple[str, str]] = []
        self.failures: dict[tuple[str, str], list[int]] = {}
        self.next_upload = 1


class FakeGateway:
    def __init__(self):
        self.state = GatewayState()
        state = self.state

        class Handler(BaseHTTPRequestHandler):
            server_version = "BackupFake/1"

            def log_message(self, _format, *_args):
                return

            def _body(self) -> bytes:
                length = int(self.headers.get("Content-Length", "0"))
                return self.rfile.read(length)

            def _json(self, status: int, payload: dict[str, object]) -> None:
                body = json.dumps(payload, separators=(",", ":")).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _record_or_fail(self) -> bool:
                path = urlparse(self.path).path
                state.requests.append((self.command, path))
                queue = state.failures.get((self.command, path), [])
                if queue:
                    status = queue.pop(0)
                    self.send_response(status)
                    if status == 429:
                        self.send_header("Retry-After", "0")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return True
                if self.path != "/healthz" and self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    self._json(401, {"error": "unauthorized"})
                    return True
                return False

            @staticmethod
            def _key(metadata: dict[str, object]) -> str:
                instant = datetime.fromisoformat(str(metadata["created_at"]).replace("Z", "+00:00"))
                stamp = instant.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
                return (
                    f"backups/{metadata['instance_id']}/{metadata['tier']}/"
                    f"{stamp}-{metadata['gzip_sha256']}.sqlite3.gz"
                )

            @staticmethod
            def _header_value(value: object) -> str:
                return str(value).replace("\r", "").replace("\n", "")

            def _object_headers(self, key: str, metadata: dict[str, object], size: int) -> None:
                self.send_header("Content-Type", "application/gzip")
                self.send_header("Content-Length", str(size))
                self.send_header("X-Backup-Key", self._header_value(key))
                for field, header in HEADER_FIELDS.items():
                    self.send_header(header, self._header_value(metadata[field]))

            def do_POST(self):
                if self._record_or_fail():
                    return
                parsed = urlparse(self.path)
                if parsed.path == "/v1/uploads":
                    metadata = json.loads(self._body())
                    key = self._key(metadata)
                    if key in state.objects:
                        self._json(200, {"key": key, "upload_id": "", "existing": True})
                        return
                    upload_id = f"upload-{state.next_upload}"
                    state.next_upload += 1
                    state.uploads[upload_id] = {"key": key, "metadata": metadata, "parts": {}}
                    self._json(201, {"key": key, "upload_id": upload_id, "existing": False})
                    return

                segments = parsed.path.strip("/").split("/")
                if len(segments) == 4 and segments[:2] == ["v1", "uploads"]:
                    upload_id, action = segments[2], segments[3]
                    upload = state.uploads.get(upload_id)
                    if upload is None:
                        self._json(404, {"error": "not found"})
                        return
                    key = parse_qs(parsed.query).get("key", [""])[0]
                    if key != upload["key"]:
                        self._json(400, {"error": "invalid request"})
                        return
                    if action == "abort":
                        del state.uploads[upload_id]
                        self.send_response(204)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    if action == "complete":
                        body = json.loads(self._body())
                        parts = body["parts"]
                        payload = b"".join(
                            upload["parts"][int(part["part_number"])] for part in parts
                        )
                        metadata = dict(upload["metadata"])
                        state.objects[key] = (payload, metadata)
                        del state.uploads[upload_id]
                        result = {"key": key, **metadata}
                        self._json(200, result)
                        return
                self._json(404, {"error": "not found"})

            def do_PUT(self):
                if self._record_or_fail():
                    return
                parsed = urlparse(self.path)
                segments = parsed.path.strip("/").split("/")
                if len(segments) == 5 and segments[:2] == ["v1", "uploads"] and segments[3] == "parts":
                    upload_id = segments[2]
                    upload = state.uploads.get(upload_id)
                    if upload is None:
                        self._json(404, {"error": "not found"})
                        return
                    key = parse_qs(parsed.query).get("key", [""])[0]
                    if key != upload["key"]:
                        self._json(400, {"error": "invalid request"})
                        return
                    part_number = int(segments[4])
                    payload = self._body()
                    upload["parts"][part_number] = payload
                    state.parts.append((part_number, len(payload)))
                    self._json(200, {"part_number": part_number, "etag": f"etag-{part_number}"})
                    return
                self._json(404, {"error": "not found"})

            def do_HEAD(self):
                if self._record_or_fail():
                    return
                parsed = urlparse(self.path)
                key = parse_qs(parsed.query).get("key", [""])[0]
                item = state.objects.get(key)
                if parsed.path != "/v1/objects" or item is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                payload, metadata = item
                self.send_response(200)
                self._object_headers(key, metadata, len(payload))
                self.end_headers()

            def do_GET(self):
                if self._record_or_fail():
                    return
                parsed = urlparse(self.path)
                if parsed.path == "/healthz":
                    self._json(200, {"ok": True})
                    return
                if parsed.path == "/v1/latest":
                    query = parse_qs(parsed.query)
                    prefix = f"backups/{query['instance'][0]}/{query['tier'][0]}/"
                    keys = sorted(key for key in state.objects if key.startswith(prefix))
                    if not keys:
                        self._json(404, {"error": "not found"})
                        return
                    key = keys[-1]
                    _payload, metadata = state.objects[key]
                    self._json(200, {"key": key, **metadata})
                    return
                if parsed.path == "/v1/objects":
                    key = parse_qs(parsed.query).get("key", [""])[0]
                    item = state.objects.get(key)
                    if item is None:
                        self._json(404, {"error": "not found"})
                        return
                    payload, metadata = item
                    self.send_response(200)
                    self._object_headers(key, metadata, len(payload))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                self._json(404, {"error": "not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class R2BackupTests(unittest.TestCase):
    @staticmethod
    def _artifact(root: Path, created_at: datetime | None = None):
        database = root / "hosted.sqlite3"
        store = Store(str(database))
        credentials = store.bootstrap("Test", "Collector")
        store.ingest(
            credentials["tenant_id"],
            credentials["collector_id"],
            [{"id": "usr_test", "displayName": "Tester", "status": "active"}],
            [],
        )
        return create_artifact(
            database,
            root / "backups",
            instance_id="test",
            created_at=created_at,
        )

    @staticmethod
    def _token(root: Path, value: str = TOKEN) -> Path:
        path = root / "backup_token"
        path.write_text(value, encoding="utf-8")
        os.chmod(path, 0o600)
        return path

    def test_upload_is_multipart_authenticated_and_verified_by_head(self):
        with tempfile.TemporaryDirectory() as directory, FakeGateway() as gateway:
            root = Path(directory)
            artifact = self._artifact(root)
            client = R2BackupClient(
                gateway.url,
                self._token(root),
                part_bytes=256,
                sleeper=lambda _seconds: None,
            )

            result = client.upload(artifact, instance_id="test", tier="hourly")

            self.assertEqual(result["gzip_sha256"], artifact.metadata["gzip_sha256"])
            self.assertGreater(len(gateway.state.parts), 1)
            self.assertTrue(all(size == 256 for _number, size in gateway.state.parts[:-1]))
            self.assertEqual(
                [number for number, _size in gateway.state.parts],
                list(range(1, len(gateway.state.parts) + 1)),
            )

    def test_client_rejects_oversized_archives_and_excess_part_counts_before_network(self):
        self.assertEqual(MAX_GZIP_BYTES, 64 * 1024**3)
        self.assertEqual(MAX_PARTS, 10_000)
        self.assertEqual(MAX_PART_BYTES, 8 * 1024 * 1024)

        with tempfile.TemporaryDirectory() as directory, FakeGateway() as gateway:
            root = Path(directory)
            base = self._artifact(root)
            oversized = BackupArtifact(
                archive=base.archive,
                manifest=base.manifest,
                metadata={**base.metadata, "gzip_bytes": MAX_GZIP_BYTES + 1},
            )
            client = R2BackupClient(gateway.url, self._token(root))
            with self.assertRaisesRegex(PermanentBackupError, "gzip_bytes"):
                client.upload(oversized, instance_id="test", tier="hourly")
            self.assertFalse(gateway.state.requests)

            archive = root / "too-many-parts.sqlite3.gz"
            payload = b"x" * (MAX_PARTS + 1)
            archive.write_bytes(payload)
            too_many = BackupArtifact(
                archive=archive,
                manifest=base.manifest,
                metadata={
                    **base.metadata,
                    "gzip_bytes": len(payload),
                    "gzip_sha256": hashlib.sha256(payload).hexdigest(),
                },
            )
            tiny_parts = R2BackupClient(gateway.url, self._token(root), part_bytes=1)
            with self.assertRaisesRegex(PermanentBackupError, "10,000 parts"):
                tiny_parts.upload(too_many, instance_id="test", tier="hourly")
            self.assertFalse(gateway.state.requests)

            bounded = R2BackupClient(
                gateway.url,
                self._token(root),
                part_bytes=MAX_PART_BYTES + 1,
            )
            self.assertEqual(bounded.part_bytes, MAX_PART_BYTES)

    def test_client_completion_json_bound_accepts_maximum_part_list_and_rejects_oversize(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = R2BackupClient("http://127.0.0.1:1", self._token(root))
            captured: list[bytes] = []

            def capture(_method, _path, **options):
                captured.append(options["data"])
                return {}

            client._execute = capture  # type: ignore[method-assign]
            maximum = {
                "parts": [
                    {"part_number": number, "etag": "a" * 256}
                    for number in range(1, MAX_PARTS + 1)
                ]
            }
            client._json_request(
                "POST",
                "/v1/uploads/upload/complete",
                payload=maximum,
                max_request_bytes=MAX_COMPLETE_JSON_BYTES,
            )
            self.assertEqual(len(captured), 1)
            self.assertLessEqual(len(captured[0]), MAX_COMPLETE_JSON_BYTES)

            with self.assertRaisesRegex(PermanentBackupError, "request is too large"):
                client._json_request(
                    "POST",
                    "/v1/uploads/upload/complete",
                    payload={"padding": "x" * MAX_COMPLETE_JSON_BYTES},
                    max_request_bytes=MAX_COMPLETE_JSON_BYTES,
                )
            self.assertEqual(len(captured), 1)

    def test_proxy_is_explicit_and_cannot_embed_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = os.environ.get("HTTPS_PROXY")
            os.environ["HTTPS_PROXY"] = "http://ambient.invalid:9999"
            try:
                direct = R2BackupClient(
                    "http://127.0.0.1:1",
                    self._token(root),
                )
                explicit = R2BackupClient(
                    "http://127.0.0.1:1",
                    self._token(root),
                    proxy_url="http://172.18.0.1:17890",
                )
            finally:
                if original is None:
                    os.environ.pop("HTTPS_PROXY", None)
                else:
                    os.environ["HTTPS_PROXY"] = original

            direct_handlers = [
                handler
                for handler in direct.opener.handlers
                if isinstance(handler, urllib.request.ProxyHandler)
            ]
            explicit_handler = next(
                handler
                for handler in explicit.opener.handlers
                if isinstance(handler, urllib.request.ProxyHandler)
            )
            self.assertFalse(any(handler.proxies for handler in direct_handlers))
            self.assertEqual(
                explicit_handler.proxies,
                {
                    "http": "http://172.18.0.1:17890",
                    "https": "http://172.18.0.1:17890",
                },
            )

            for invalid in (
                "socks5://127.0.0.1:7890",
                "http://user:secret@127.0.0.1:7890",
                "http://127.0.0.1:7890/path",
            ):
                with self.subTest(invalid=invalid), self.assertRaisesRegex(
                    PermanentBackupError,
                    "proxy URL",
                ):
                    R2BackupClient(
                        "http://127.0.0.1:1",
                        self._token(root),
                        proxy_url=invalid,
                    )

    def test_retry_honors_429_but_permanent_403_is_not_retried(self):
        with tempfile.TemporaryDirectory() as directory, FakeGateway() as gateway:
            root = Path(directory)
            artifact = self._artifact(root)
            sleeps: list[float] = []
            gateway.state.failures[("POST", "/v1/uploads")] = [429]
            client = R2BackupClient(
                gateway.url,
                self._token(root),
                attempts=3,
                sleeper=sleeps.append,
            )
            client.upload(artifact, instance_id="test", tier="hourly")
            self.assertEqual(sleeps, [0.0])

        with tempfile.TemporaryDirectory() as directory, FakeGateway() as gateway:
            root = Path(directory)
            artifact = self._artifact(root)
            gateway.state.failures[("POST", "/v1/uploads")] = [403]
            client = R2BackupClient(
                gateway.url,
                self._token(root),
                attempts=3,
                sleeper=lambda _seconds: self.fail("403 must not be retried"),
            )
            with self.assertRaises(PermanentBackupError):
                client.upload(artifact, instance_id="test", tier="hourly")
            creates = [item for item in gateway.state.requests if item == ("POST", "/v1/uploads")]
            self.assertEqual(len(creates), 1)

    def test_insecure_token_permissions_are_rejected_before_network(self):
        with tempfile.TemporaryDirectory() as directory, FakeGateway() as gateway:
            root = Path(directory)
            artifact = self._artifact(root)
            token = self._token(root)
            os.chmod(token, 0o622)
            client = R2BackupClient(gateway.url, token)
            with self.assertRaisesRegex(PermanentBackupError, "permissions"):
                client.upload(artifact, instance_id="test", tier="hourly")
            self.assertFalse(gateway.state.requests)

    def test_sync_is_idempotent_and_creates_three_retention_tiers(self):
        with tempfile.TemporaryDirectory() as directory, FakeGateway() as gateway:
            root = Path(directory)
            artifact = self._artifact(
                root,
                created_at=datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc),
            )
            state_dir = root / "state"
            state_dir.mkdir()
            client = R2BackupClient(gateway.url, self._token(root))

            first = sync_once(root / "backups", state_dir, client, "test")
            request_count = len(gateway.state.requests)
            second = sync_once(root / "backups", state_dir, client, "test")

            self.assertEqual(first["uploaded"], 3)
            self.assertEqual(second["uploaded"], 0)
            self.assertEqual(len(gateway.state.requests), request_count)
            self.assertEqual(
                {key.split("/")[2] for key in gateway.state.objects},
                {"hourly", "daily", "monthly"},
            )
            self.assertTrue((state_dir / ".last-offsite-success.json").is_file())
            self.assertEqual(artifact.metadata["gzip_sha256"], first["gzip_sha256"])

    def test_restore_drill_downloads_and_revalidates_the_exact_object(self):
        with tempfile.TemporaryDirectory() as directory, FakeGateway() as gateway:
            root = Path(directory)
            artifact = self._artifact(root)
            state_dir = root / "state"
            state_dir.mkdir()
            client = R2BackupClient(gateway.url, self._token(root))
            uploaded = client.upload(artifact, instance_id="test", tier="hourly")

            report = client.restore_drill(uploaded["key"], uploaded, state_dir)

            self.assertEqual(report["integrity"], "ok")
            self.assertEqual(report["table_counts"]["friends"], 1)
            marker = json.loads((state_dir / ".last-restore-drill.json").read_text())
            self.assertEqual(marker["key"], uploaded["key"])
            self.assertEqual(marker["gzip_sha256"], artifact.metadata["gzip_sha256"])
            self.assertFalse(list(state_dir.glob("*.sqlite3")))

    def test_health_requires_recent_upload_and_restore_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            now = datetime(2026, 8, 27, 16, 0, tzinfo=timezone.utc)
            recent = (now - timedelta(minutes=30)).isoformat()
            (state_dir / ".last-offsite-success.json").write_text(
                json.dumps({"completed_at": recent}), encoding="utf-8"
            )
            (state_dir / ".last-restore-drill.json").write_text(
                json.dumps({"completed_at": recent}), encoding="utf-8"
            )
            self.assertTrue(
                offsite_backup_healthy(
                    state_dir,
                    max_upload_age_seconds=7200,
                    max_drill_age_seconds=93600,
                    now=now,
                )
            )
            stale = (now - timedelta(days=2)).isoformat()
            (state_dir / ".last-restore-drill.json").write_text(
                json.dumps({"completed_at": stale}), encoding="utf-8"
            )
            self.assertFalse(
                offsite_backup_healthy(
                    state_dir,
                    max_upload_age_seconds=7200,
                    max_drill_age_seconds=93600,
                    now=now,
                )
            )


if __name__ == "__main__":
    unittest.main()
