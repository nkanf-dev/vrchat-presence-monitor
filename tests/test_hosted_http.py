from __future__ import annotations

import gzip
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import server.app as app_module
import server.backup_json as backup_json_module
from server.app import create_app
from server.settings import Settings
from server.storage import Store


class HostedHttpContractTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        static_dir = Path(self.directory.name) / "static"
        static_dir.mkdir()
        (static_dir / "index.html").write_text(
            '<!doctype html><html lang="zh-CN"><body><div id="root"></div></body></html>',
            encoding="utf-8",
        )
        self.settings = Settings(
            data_dir=Path(self.directory.name),
            static_dir=static_dir,
            bootstrap_token="bootstrap-secret",
            cookie_secure="never",
            trust_proxy_headers=False,
            session_days=30,
            login_attempts=3,
            login_window_seconds=300,
            max_import_bytes=1024 * 1024,
            max_import_expanded_bytes=2 * 1024 * 1024,
            import_requests=2,
            import_window_seconds=60,
        )
        self.store = Store(
            str(self.settings.data_dir / "hosted.sqlite3"),
            max_backup_bytes=self.settings.max_import_bytes,
        )
        self.client = TestClient(create_app(self.settings, self.store))
        response = self.client.post(
            "/v1/bootstrap",
            headers={"X-Bootstrap-Token": "bootstrap-secret"},
            json={"tenant_name": "Alice 的监控", "collector_name": "Alice bridge"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.bootstrap = response.json()

    def tearDown(self):
        self.client.close()
        self.directory.cleanup()

    def login(self, access_code=None):
        return self.client.post(
            "/v1/login",
            headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
            json={"access_code": access_code or self.bootstrap["access_code"]},
        )

    @staticmethod
    def admin_headers():
        return {"X-Bootstrap-Token": "bootstrap-secret"}

    def test_liveness_readiness_and_static_security_headers(self):
        self.assertEqual(self.client.get("/livez").json(), {"ok": True})
        with patch.object(
            app_module.shutil,
            "disk_usage",
            return_value=SimpleNamespace(free=self.settings.minimum_free_bytes + 1),
        ):
            self.assertEqual(self.client.get("/readyz").json(), {"ok": True})
        with patch.object(
            app_module.shutil,
            "disk_usage",
            return_value=SimpleNamespace(free=max(0, self.settings.minimum_free_bytes - 1)),
        ):
            self.assertEqual(self.client.get("/readyz").status_code, 503)

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        content_security_policy = response.headers["content-security-policy"]
        self.assertIn("default-src 'self'", content_security_policy)
        self.assertIn("script-src 'self'", content_security_policy)
        self.assertNotIn("script-src 'self' 'unsafe-inline'", content_security_policy)
        self.assertIn("style-src 'self'", content_security_policy)
        self.assertIn("style-src-attr 'unsafe-inline'", content_security_policy)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["cross-origin-opener-policy"], "same-origin")
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_login_uses_http_only_cookie_and_logout_revokes_current_session(self):
        response = self.login()

        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("session_token", response.json())
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("presence_session=", cookie)
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)
        self.assertEqual(self.client.get("/v1/me").status_code, 200)

        logout = self.client.post(
            "/v1/logout",
            headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
        )

        self.assertEqual(logout.status_code, 200)
        self.assertEqual(self.client.get("/v1/me").status_code, 401)

    def test_authenticated_capabilities_report_the_actual_import_limits(self):
        self.assertEqual(self.client.get("/v1/capabilities").status_code, 401)
        self.assertEqual(self.login().status_code, 200)
        response = self.client.get("/v1/capabilities")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(),
            {
                "max_import_bytes": self.settings.max_import_bytes,
                "max_import_expanded_bytes": self.settings.max_import_expanded_bytes,
                "max_source_expanded_bytes": self.settings.max_source_expanded_bytes,
            },
        )

    def test_legacy_viewer_bearer_is_adopted_once_into_cookie(self):
        session = self.store.exchange_access_code(self.bootstrap["access_code"])
        assert session is not None

        response = self.client.get(
            "/v1/me", headers={"Authorization": f"Bearer {session['session_token']}"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["migrated"])
        self.assertIn("httponly", response.headers["set-cookie"].lower())
        self.assertEqual(self.client.get("/v1/me").status_code, 200)

    def test_cross_site_mutations_are_rejected(self):
        response = self.client.post(
            "/v1/login",
            headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
            json={"access_code": self.bootstrap["access_code"]},
        )
        self.assertEqual(response.status_code, 403)

    def test_login_rate_limit_is_bounded_and_does_not_reveal_code_state(self):
        for _ in range(3):
            response = self.client.post("/v1/login", json={"access_code": "WRONG-WRONG-WRONG"})
            self.assertEqual(response.status_code, 401)

        limited = self.client.post("/v1/login", json={"access_code": "WRONG-WRONG-WRONG"})

        self.assertEqual(limited.status_code, 429)
        self.assertIn("retry-after", limited.headers)

    def test_normalized_telemetry_is_tenant_scoped_and_server_sessions_are_disabled(self):
        payload = {
            "schema_version": 1,
            "friends": [
                {
                    "id": "usr_1",
                    "displayName": "Alice",
                    "status": "active",
                    "location": "wrld_example:1",
                }
            ],
            "events": [],
        }
        unauthorized = self.client.post("/v1/telemetry", json=payload)
        accepted = self.client.post(
            "/v1/telemetry",
            headers={"Authorization": f"Bearer {self.bootstrap['collector_token']}"},
            json=payload,
        )

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(self.client.get("/v1/session-key").status_code, 404)
        self.assertEqual(self.client.post("/v1/collector/session", json={}).status_code, 404)

        self.assertEqual(self.login().status_code, 200)
        overview = self.client.get("/v1/overview").json()
        people = self.client.get("/v1/friends?limit=10&q=ali").json()
        self.assertEqual(overview["tracked_count"], 1)
        self.assertEqual(people["total"], 1)
        self.assertEqual(people["items"][0]["display_name"], "Alice")

    def test_bootstrap_does_not_issue_a_permanent_viewer_token(self):
        self.assertNotIn("viewer_token", self.bootstrap)

    def test_every_management_endpoint_requires_the_bootstrap_token(self):
        tenant_id = self.bootstrap["tenant_id"]
        collector_id = self.bootstrap["collector_id"]
        endpoints = [
            ("POST", "/v1/bootstrap"),
            ("POST", f"/v1/admin/tenants/{tenant_id}/access-code/rotate"),
            ("DELETE", f"/v1/admin/tenants/{tenant_id}/access-code"),
            ("POST", f"/v1/admin/tenants/{tenant_id}/collectors/{collector_id}/token/rotate"),
            ("DELETE", f"/v1/admin/tenants/{tenant_id}/collectors/{collector_id}/token"),
            ("POST", f"/v1/admin/tenants/{tenant_id}/viewer-sessions/revoke-all"),
            ("GET", f"/v1/admin/tenants/{tenant_id}/security-audit"),
        ]
        for method, path in endpoints:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.client.request(method, path).status_code, 403)
                self.assertEqual(
                    self.client.request(
                        method, path, headers={"X-Bootstrap-Token": "wrong"}
                    ).status_code,
                    403,
                )

    def test_management_lifecycle_is_atomic_scoped_and_audited(self):
        self.assertEqual(self.login().status_code, 200)
        tenant_id = self.bootstrap["tenant_id"]
        collector_id = self.bootstrap["collector_id"]
        headers = self.admin_headers()

        access_rotation = self.client.post(
            f"/v1/admin/tenants/{tenant_id}/access-code/rotate", headers=headers
        )
        self.assertEqual(access_rotation.status_code, 200, access_rotation.text)
        new_access_code = access_rotation.json()["access_code"]
        self.assertEqual(self.login(self.bootstrap["access_code"]).status_code, 401)
        self.assertEqual(self.client.get("/v1/me").status_code, 200)
        self.assertEqual(self.login(new_access_code).status_code, 200)

        revoke_sessions = self.client.post(
            f"/v1/admin/tenants/{tenant_id}/viewer-sessions/revoke-all", headers=headers
        )
        self.assertEqual(revoke_sessions.status_code, 200, revoke_sessions.text)
        self.assertEqual(revoke_sessions.json()["revoked_count"], 2)
        self.assertEqual(self.client.get("/v1/me").status_code, 401)

        collector_rotation = self.client.post(
            f"/v1/admin/tenants/{tenant_id}/collectors/{collector_id}/token/rotate",
            headers=headers,
        )
        self.assertEqual(collector_rotation.status_code, 200, collector_rotation.text)
        new_collector_token = collector_rotation.json()["collector_token"]
        telemetry = {"schema_version": 1, "friends": [], "events": []}
        self.assertEqual(
            self.client.post(
                "/v1/telemetry",
                headers={"Authorization": f"Bearer {self.bootstrap['collector_token']}"},
                json=telemetry,
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.post(
                "/v1/telemetry",
                headers={"Authorization": f"Bearer {new_collector_token}"},
                json=telemetry,
            ).status_code,
            200,
        )
        collector_revoke = self.client.delete(
            f"/v1/admin/tenants/{tenant_id}/collectors/{collector_id}/token",
            headers=headers,
        )
        self.assertEqual(collector_revoke.status_code, 200)
        self.assertTrue(collector_revoke.json()["revoked"])
        collector_revoke_retry = self.client.delete(
            f"/v1/admin/tenants/{tenant_id}/collectors/{collector_id}/token",
            headers=headers,
        )
        self.assertEqual(collector_revoke_retry.status_code, 200)
        self.assertFalse(collector_revoke_retry.json()["revoked"])
        self.assertEqual(
            self.client.post(
                "/v1/telemetry",
                headers={"Authorization": f"Bearer {new_collector_token}"},
                json=telemetry,
            ).status_code,
            401,
        )

        revoke_codes = self.client.delete(
            f"/v1/admin/tenants/{tenant_id}/access-code", headers=headers
        )
        self.assertEqual(revoke_codes.status_code, 200, revoke_codes.text)
        self.assertEqual(self.login(new_access_code).status_code, 401)

        audit = self.client.get(
            f"/v1/admin/tenants/{tenant_id}/security-audit", headers=headers
        )
        self.assertEqual(audit.status_code, 200, audit.text)
        actions = {item["action"] for item in audit.json()["items"]}
        self.assertTrue(
            {
                "access_code.rotate",
                "viewer_session.revoke_all",
                "collector_token.rotate",
                "collector_token.revoke",
                "access_code.revoke",
            }.issubset(actions)
        )
        rendered = audit.text
        for secret in (
            self.bootstrap["access_code"],
            self.bootstrap["collector_token"],
            new_access_code,
            new_collector_token,
        ):
            self.assertNotIn(secret, rendered)

    def test_import_json_decode_runs_off_loop_and_rate_limit_is_tenant_scoped(self):
        self.assertEqual(self.login().status_code, 200)
        payload = {
            "format": "vrchat-monitor-hosted-backup",
            "version": 1,
            "friends": [],
            "status_events": [],
        }
        event_loop_threads = []
        decode_threads = []
        real_read = app_module._read_bytes
        real_decode = app_module._decode_backup

        async def observed_read(*args, **kwargs):
            event_loop_threads.append(threading.get_ident())
            return await real_read(*args, **kwargs)

        def observed_decode(*args, **kwargs):
            decode_threads.append(threading.get_ident())
            return real_decode(*args, **kwargs)

        request_headers = {
            "Origin": "http://testserver",
            "Sec-Fetch-Site": "same-origin",
            "Content-Type": "application/json",
        }
        with patch.object(app_module, "_read_bytes", new=observed_read), patch.object(
            app_module, "_decode_backup", new=observed_decode
        ):
            first = self.client.post(
                "/v1/import.json",
                headers=request_headers,
                content=json.dumps(payload).encode("utf-8"),
            )

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(len(event_loop_threads), 1)
        self.assertEqual(len(decode_threads), 1)
        self.assertNotEqual(event_loop_threads[0], decode_threads[0])

        second = self.client.post("/v1/import.json", headers=request_headers, json=payload)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(self.login().status_code, 200)
        limited = self.client.post("/v1/import.json", headers=request_headers, json=payload)
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.headers["retry-after"], "60")

    def test_import_accepts_gzip_local_v2_backup_and_rejects_corruption(self):
        self.assertEqual(self.login().status_code, 200)
        payload = {
            "format": "vrchat-monitor-backup",
            "version": 2,
            "friends": [
                {
                    "id": "usr_1",
                    "username": "alice",
                    "display_name": "Alice",
                    "status": "active",
                    "updated_at": "2026-08-27T12:00:00+00:00",
                }
            ],
            "status_events": [
                {
                    "client_event_id": "event_0123456789abcdef0123456789abcdef",
                    "friend_id": "usr_1",
                    "occurred_at": "2026-08-27T12:00:00+00:00",
                    "old_status": "offline",
                    "new_status": "active",
                }
            ],
            "sync_runs": [],
            "raw_fetches": [{"body_b64": "ignored-by-hosted-import"}],
        }
        headers = {
            "Origin": "http://testserver",
            "Sec-Fetch-Site": "same-origin",
            "Content-Type": "application/gzip",
        }
        response = self.client.post(
            "/v1/import.json",
            headers=headers,
            content=gzip.compress(json.dumps(payload).encode("utf-8")),
        )
        corrupt = self.client.post(
            "/v1/import.json",
            headers=headers,
            content=b"\x1f\x8bbroken",
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["imported"], {"friends": 1, "events": 1, "changed": 0})
        self.assertEqual(corrupt.status_code, 400)

    def test_direct_import_rejects_duplicate_json_fields(self):
        self.assertEqual(self.login().status_code, 200)
        response = self.client.post(
            "/v1/import.json",
            headers={
                "Origin": "http://testserver",
                "Sec-Fetch-Site": "same-origin",
                "Content-Type": "application/json",
            },
            content=(
                b'{"format":"vrchat-monitor-backup","version":2,'
                b'"friends":[],"friends":[],"status_events":[]}'
            ),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("重复字段", response.text)

    def test_streaming_decoder_rejects_a_million_tiny_objects_before_materializing_them(self):
        tiny_objects = b",".join([b"{}"] * 1_000_000)
        encoded = (
            b'{"format":"vrchat-monitor-hosted-backup","version":2,'
            b'"friends":['
            + tiny_objects
            + b'],"status_events":[]}'
        )

        with self.assertRaisesRegex(ValueError, "JSON 对象过多"):
            app_module._decode_backup(encoded, len(encoded) + 1)

    def test_streaming_decoder_rejects_excessive_depth_with_a_specific_error(self):
        encoded = (
            b'{"format":"vrchat-monitor-hosted-backup","version":2,'
            b'"friends":[],"status_events":[],"padding":'
            + b"[" * 80
            + b"0"
            + b"]" * 80
            + b"}"
        )

        with self.assertRaisesRegex(ValueError, "JSON 嵌套层级过深"):
            app_module._decode_backup(encoded, len(encoded) + 1)

    def test_streaming_decoder_enforces_the_materialized_memory_budget(self):
        encoded = json.dumps(
            {
                "format": "vrchat-monitor-hosted-backup",
                "version": 2,
                "friends": [
                    {"id": f"usr_{index}", "display_name": "x" * 200}
                    for index in range(20)
                ],
                "status_events": [],
            },
            separators=(",", ":"),
        ).encode("utf-8")

        with patch.object(backup_json_module, "MAX_MATERIALIZED_BYTES", 1024):
            with self.assertRaisesRegex(ValueError, "JSON 内存放大过高"):
                app_module._decode_backup(encoded, len(encoded) + 1)

    def test_streaming_limit_failure_does_not_partially_import(self):
        self.assertEqual(self.login().status_code, 200)
        tiny_objects = b",".join([b"{}"] * 250_000)
        encoded = (
            b'{"format":"vrchat-monitor-hosted-backup","version":2,'
            b'"friends":[{"id":"usr_must_not_persist","display_name":"Nope"},'
            + tiny_objects
            + b'],"status_events":[]}'
        )
        response = self.client.post(
            "/v1/import.json",
            headers={
                "Origin": "http://testserver",
                "Sec-Fetch-Site": "same-origin",
                "Content-Type": "application/json",
            },
            content=encoded,
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("JSON 对象过多", response.text)
        self.assertEqual(self.client.get("/v1/friends?q=must_not_persist").json()["total"], 0)

    def test_streaming_import_round_trips_a_real_hosted_backup_through_gzip(self):
        collector = self.store.auth(self.bootstrap["collector_token"], "collector")
        assert collector is not None
        self.store.ingest(
            collector["tenant_id"],
            collector["id"],
            [
                {
                    "id": "usr_real",
                    "username": "real-user",
                    "displayName": "Real User",
                    "status": "active",
                    "location": "wrld_real:1",
                }
            ],
            [
                {
                    "client_event_id": "event_real_001",
                    "friend_id": "usr_real",
                    "occurred_at": "2026-08-28T12:00:00+00:00",
                    "old_status": "offline",
                    "new_status": "active",
                    "location": "wrld_real:1",
                    "source": "local-bridge",
                }
            ],
        )
        exported = self.store.export_json(collector["tenant_id"])
        target = self.store.bootstrap("Restore target", "target bridge")
        self.assertEqual(self.login(target["access_code"]).status_code, 200)

        response = self.client.post(
            "/v1/import.json",
            headers={
                "Origin": "http://testserver",
                "Sec-Fetch-Site": "same-origin",
                "Content-Type": "application/gzip",
            },
            content=gzip.compress(
                json.dumps(exported, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ),
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["imported"]["friends"], 1)
        self.assertEqual(response.json()["imported"]["events"], 1)

    def test_export_is_within_the_same_limit_used_for_plain_and_gzip_restore(self):
        self.assertEqual(self.login().status_code, 200)
        exported = self.client.get("/v1/export.json")
        headers = {
            "Origin": "http://testserver",
            "Sec-Fetch-Site": "same-origin",
            "Content-Type": "application/json",
        }
        restored = self.client.post(
            "/v1/import.json",
            headers=headers,
            content=exported.content,
        )
        oversized = gzip.compress(
            json.dumps(
                {
                    "format": "vrchat-monitor-hosted-backup",
                    "version": 2,
                    "friends": [],
                    "status_events": [],
                    "padding": "x" * self.settings.max_import_expanded_bytes,
                }
            ).encode("utf-8")
        )
        rejected = self.client.post(
            "/v1/import.json",
            headers={**headers, "Content-Type": "application/gzip"},
            content=oversized,
        )

        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertLessEqual(len(exported.content), self.settings.max_import_bytes)
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(rejected.status_code, 400, rejected.text)
        self.assertIn("解压后过大", rejected.text)

    def test_old_over_limit_database_starts_and_round_trips_through_gzip_export(self):
        collector = self.store.auth(self.bootstrap["collector_token"], "collector")
        assert collector is not None
        self.store.ingest(
            collector["tenant_id"],
            collector["id"],
            [{"id": "usr_large", "displayName": "Large", "bio": "x" * 8192}],
            [],
        )
        low_settings = replace(
            self.settings,
            max_import_bytes=1024,
            max_import_expanded_bytes=64 * 1024,
        )
        reopened = Store(
            str(self.settings.data_dir / "hosted.sqlite3"),
            max_backup_bytes=low_settings.max_import_bytes,
        )
        target = reopened.bootstrap("Restore target", "bridge")
        with TestClient(create_app(low_settings, reopened)) as client:
            login_headers = {
                "Origin": "http://testserver",
                "Sec-Fetch-Site": "same-origin",
            }
            self.assertEqual(
                client.post(
                    "/v1/login",
                    headers=login_headers,
                    json={"access_code": self.bootstrap["access_code"]},
                ).status_code,
                200,
            )
            exported = client.get("/v1/export.json")
            self.assertEqual(exported.status_code, 200, exported.text)
            self.assertEqual(exported.headers["content-type"], "application/gzip")
            self.assertLessEqual(len(exported.content), low_settings.max_import_bytes)
            decoded = json.loads(gzip.decompress(exported.content))
            self.assertEqual(decoded["friends"][0]["id"], "usr_large")

            self.assertEqual(
                client.post(
                    "/v1/login",
                    headers=login_headers,
                    json={"access_code": target["access_code"]},
                ).status_code,
                200,
            )
            restored = client.post(
                "/v1/import.json",
                headers={**login_headers, "Content-Type": "application/gzip"},
                content=exported.content,
            )
            self.assertEqual(restored.status_code, 200, restored.text)
            self.assertEqual(restored.json()["imported"]["friends"], 1)

    def test_login_rejects_vrchat_password_fields(self):
        response = self.client.post(
            "/v1/login",
            json={
                "access_code": self.bootstrap["access_code"],
                "username": "not-accepted",
                "password": "not-accepted",
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_malformed_backup_shapes_fail_closed_without_server_errors(self):
        self.assertEqual(self.login().status_code, 200)
        headers = {
            "Origin": "http://testserver",
            "Sec-Fetch-Site": "same-origin",
            "Content-Type": "application/json",
        }
        invalid_version = self.client.post(
            "/v1/import.json",
            headers=headers,
            json={
                "format": "vrchat-monitor-hosted-backup",
                "version": {},
                "friends": [],
                "status_events": [],
            },
        )
        invalid_item = self.client.post(
            "/v1/import.json",
            headers=headers,
            json={
                "format": "vrchat-monitor-hosted-backup",
                "version": 1,
                "friends": ["not-an-object"],
                "status_events": [],
            },
        )

        self.assertEqual(invalid_version.status_code, 400)
        self.assertEqual(invalid_item.status_code, 400)

    def test_excessively_nested_backup_json_returns_a_bounded_client_error(self):
        self.assertEqual(self.login().status_code, 200)
        response = self.client.post(
            "/v1/import.json",
            headers={
                "Origin": "http://testserver",
                "Sec-Fetch-Site": "same-origin",
                "Content-Type": "application/json",
            },
            content=b"[" * 1500 + b"0" + b"]" * 1500,
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
