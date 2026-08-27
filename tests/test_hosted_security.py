from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from server.storage import Store


class HostedCredentialLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(f"{self.directory.name}/hosted.sqlite3")
        self.tenant = self.store.bootstrap("one", "collector")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_access_code_rotation_is_atomic_and_does_not_hide_session_revocation(self):
        existing_session = self.store.exchange_access_code(self.tenant["access_code"])
        self.assertIsNotNone(existing_session)

        rotated = self.store.rotate_access_code(self.tenant["tenant_id"])

        self.assertIsNone(self.store.exchange_access_code(self.tenant["access_code"]))
        self.assertIsNotNone(self.store.exchange_access_code(rotated["access_code"]))
        self.assertIsNotNone(self.store.auth(existing_session["session_token"], "viewer"))
        self.assertEqual(rotated["revoked_count"], 1)

        audit = self.store.security_audit(self.tenant["tenant_id"])
        self.assertIn("access_code.rotate", [item["action"] for item in audit["items"]])
        rendered = repr(audit)
        self.assertNotIn(self.tenant["access_code"], rendered)
        self.assertNotIn(rotated["access_code"], rendered)

    def test_access_code_rotation_rolls_back_if_replacement_cannot_be_stored(self):
        before = self.store.security_audit(self.tenant["tenant_id"])["total"]
        duplicate = self.tenant["access_code"]
        with patch.object(
            self.store,
            "_new_access_code",
            return_value=(duplicate, self.store.hash_token(duplicate)),
        ):
            with self.assertRaises(sqlite3.IntegrityError):
                self.store.rotate_access_code(self.tenant["tenant_id"])

        self.assertIsNotNone(self.store.exchange_access_code(duplicate))
        self.assertEqual(self.store.security_audit(self.tenant["tenant_id"])["total"], before)

    def test_access_code_rotation_rolls_back_if_audit_append_fails(self):
        before = self.store.security_audit(self.tenant["tenant_id"])["total"]
        with self.store.connection() as db:
            db.execute(
                """CREATE TRIGGER reject_access_rotation_audit
                BEFORE INSERT ON security_audit
                WHEN NEW.action='access_code.rotate'
                BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END"""
            )

        with self.assertRaisesRegex(sqlite3.IntegrityError, "audit unavailable"):
            self.store.rotate_access_code(self.tenant["tenant_id"])

        self.assertIsNotNone(self.store.exchange_access_code(self.tenant["access_code"]))
        self.assertEqual(self.store.security_audit(self.tenant["tenant_id"])["total"], before)

    def test_concurrent_login_is_linearized_before_access_code_rotation(self):
        login_store = Store(self.store.path)
        exchange_reached_issue = threading.Event()
        release_exchange = threading.Event()
        rotation_started = threading.Event()
        rotation_finished = threading.Event()
        results = {}
        errors = []
        original_new_token = login_store._new_token

        def paused_new_token():
            exchange_reached_issue.set()
            if not release_exchange.wait(2):
                raise TimeoutError("test did not release access-code exchange")
            return original_new_token()

        def exchange():
            try:
                results["session"] = login_store.exchange_access_code(
                    self.tenant["access_code"]
                )
            except Exception as error:  # pragma: no cover - surfaced below
                errors.append(error)

        def rotate():
            rotation_started.set()
            try:
                results["rotation"] = self.store.rotate_access_code(
                    self.tenant["tenant_id"]
                )
            except Exception as error:  # pragma: no cover - surfaced below
                errors.append(error)
            finally:
                rotation_finished.set()

        with patch.object(login_store, "_new_token", side_effect=paused_new_token):
            exchange_thread = threading.Thread(target=exchange)
            rotation_thread = threading.Thread(target=rotate)
            exchange_thread.start()
            self.assertTrue(exchange_reached_issue.wait(2))
            rotation_thread.start()
            self.assertTrue(rotation_started.wait(2))
            self.assertFalse(rotation_finished.wait(0.1))
            release_exchange.set()
            exchange_thread.join(2)
            rotation_thread.join(2)

        self.assertFalse(exchange_thread.is_alive())
        self.assertFalse(rotation_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertIsNotNone(results["session"])
        self.assertIsNotNone(
            self.store.auth(results["session"]["session_token"], "viewer")
        )
        self.assertIsNone(self.store.exchange_access_code(self.tenant["access_code"]))
        self.assertIsNotNone(
            self.store.exchange_access_code(results["rotation"]["access_code"])
        )

    def test_collector_rotation_is_atomic_precise_and_revocable(self):
        other = self.store.bootstrap("two", "other collector")
        old_token = self.tenant["collector_token"]

        rotated = self.store.rotate_collector_token(
            self.tenant["tenant_id"], self.tenant["collector_id"]
        )

        self.assertIsNone(self.store.auth(old_token, "collector"))
        current = self.store.auth(rotated["collector_token"], "collector")
        self.assertIsNotNone(current)
        self.assertEqual(current["id"], self.tenant["collector_id"])
        with self.assertRaises(KeyError):
            self.store.rotate_collector_token(
                self.tenant["tenant_id"], other["collector_id"]
            )
        self.assertIsNotNone(self.store.auth(other["collector_token"], "collector"))

        self.assertTrue(
            self.store.revoke_collector(self.tenant["tenant_id"], self.tenant["collector_id"])
        )
        self.assertFalse(
            self.store.revoke_collector(self.tenant["tenant_id"], self.tenant["collector_id"])
        )
        self.assertIsNone(self.store.auth(rotated["collector_token"], "collector"))

    def test_collector_rotation_rolls_back_on_hash_collision(self):
        other = self.store.bootstrap("two", "other collector")
        old_token = self.tenant["collector_token"]
        before = self.store.security_audit(self.tenant["tenant_id"])["total"]
        with patch.object(
            self.store,
            "_new_token",
            return_value=(
                other["collector_token"],
                self.store.hash_token(other["collector_token"]),
            ),
        ):
            with self.assertRaises(sqlite3.IntegrityError):
                self.store.rotate_collector_token(
                    self.tenant["tenant_id"], self.tenant["collector_id"]
                )

        self.assertIsNotNone(self.store.auth(old_token, "collector"))
        self.assertIsNotNone(self.store.auth(other["collector_token"], "collector"))
        self.assertEqual(self.store.security_audit(self.tenant["tenant_id"])["total"], before)

    def test_explicit_revocation_is_tenant_scoped_and_audited_without_secrets(self):
        first = self.store.exchange_access_code(self.tenant["access_code"])
        second = self.store.exchange_access_code(self.tenant["access_code"])
        other = self.store.bootstrap("two", "other collector")
        other_session = self.store.exchange_access_code(other["access_code"])
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNotNone(other_session)

        revoked_sessions = self.store.revoke_all_viewer_sessions(self.tenant["tenant_id"])
        revoked_codes = self.store.revoke_access_codes(self.tenant["tenant_id"])

        self.assertEqual(revoked_sessions, 2)
        self.assertEqual(revoked_codes, 1)
        self.assertIsNone(self.store.auth(first["session_token"], "viewer"))
        self.assertIsNone(self.store.auth(second["session_token"], "viewer"))
        self.assertIsNotNone(self.store.auth(other_session["session_token"], "viewer"))
        self.assertIsNone(self.store.exchange_access_code(self.tenant["access_code"]))
        self.assertIsNotNone(self.store.exchange_access_code(other["access_code"]))

        audit = self.store.security_audit(self.tenant["tenant_id"])
        actions = {item["action"] for item in audit["items"]}
        self.assertIn("viewer_session.revoke_all", actions)
        self.assertIn("access_code.revoke", actions)
        rendered = repr(audit)
        for secret in (
            self.tenant["access_code"],
            self.tenant["collector_token"],
            first["session_token"],
            second["session_token"],
        ):
            self.assertNotIn(secret, rendered)
        self.assertNotIn(other["tenant_id"], rendered)

    def test_revocations_roll_back_when_their_audit_record_cannot_be_appended(self):
        session = self.store.exchange_access_code(self.tenant["access_code"])
        self.assertIsNotNone(session)
        before = self.store.security_audit(self.tenant["tenant_id"])["total"]
        with self.store.connection() as db:
            db.execute(
                """CREATE TRIGGER reject_revocation_audit
                BEFORE INSERT ON security_audit
                WHEN NEW.action IN (
                    'access_code.revoke',
                    'collector_token.revoke',
                    'viewer_session.revoke_all'
                )
                BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END"""
            )

        with self.assertRaisesRegex(sqlite3.IntegrityError, "audit unavailable"):
            self.store.revoke_access_codes(self.tenant["tenant_id"])
        with self.assertRaisesRegex(sqlite3.IntegrityError, "audit unavailable"):
            self.store.revoke_collector(
                self.tenant["tenant_id"], self.tenant["collector_id"]
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "audit unavailable"):
            self.store.revoke_all_viewer_sessions(self.tenant["tenant_id"])

        self.assertIsNotNone(self.store.exchange_access_code(self.tenant["access_code"]))
        self.assertIsNotNone(self.store.auth(self.tenant["collector_token"], "collector"))
        self.assertIsNotNone(self.store.auth(session["session_token"], "viewer"))
        self.assertEqual(self.store.security_audit(self.tenant["tenant_id"])["total"], before)


if __name__ == "__main__":
    unittest.main()
