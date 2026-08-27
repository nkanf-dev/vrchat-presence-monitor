from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import server.app as app_module
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
            import_requests=2,
            import_window_seconds=60,
        )
        self.store = Store(str(self.settings.data_dir / "hosted.sqlite3"))
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
        self.assertIn("default-src 'self'", response.headers["content-security-policy"])
        self.assertNotIn("unsafe-inline", response.headers["content-security-policy"])
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
