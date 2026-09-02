import tempfile
import unittest

from server.storage import Store


class HostedIsolationTests(unittest.TestCase):
    def test_access_code_exchanges_for_scoped_viewer_session(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(f"{directory}/hosted.sqlite3")
            first = store.bootstrap("one", "collector")
            second = store.bootstrap("two", "collector")

            self.assertNotEqual(first["access_code"], second["access_code"])
            session = store.exchange_access_code(first["access_code"])

            self.assertIsNotNone(session)
            viewer = store.auth(session["session_token"], "viewer")
            self.assertEqual(viewer["tenant_id"], first["tenant_id"])
            compact_code = first["access_code"].replace("-", "").lower()
            self.assertIsNotNone(store.exchange_access_code(compact_code))
            self.assertIsNone(store.exchange_access_code("not-a-real-access-code"))
            with store.connection() as db:
                stored = db.execute("SELECT code_hash FROM login_codes WHERE tenant_id=?", (first["tenant_id"],)).fetchone()
                self.assertNotEqual(stored["code_hash"], first["access_code"])

    def test_expired_browser_session_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(f"{directory}/hosted.sqlite3")
            bootstrapped = store.bootstrap("one", "collector")
            session = store.exchange_access_code(bootstrapped["access_code"])
            with store.connection() as db:
                db.execute("UPDATE viewer_tokens SET expires_at='2000-01-01T00:00:00+00:00' WHERE token_hash IS NOT NULL AND expires_at IS NOT NULL")

            self.assertIsNone(store.auth(session["session_token"], "viewer"))
            self.assertIsNone(store.viewer_identity(session["session_token"]))

    def test_logout_revokes_only_current_browser_session(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(f"{directory}/hosted.sqlite3")
            bootstrapped = store.bootstrap("one", "collector")
            first = store.exchange_access_code(bootstrapped["access_code"])
            second = store.exchange_access_code(bootstrapped["access_code"])

            self.assertTrue(store.revoke_viewer(first["session_token"]))
            self.assertIsNone(store.auth(first["session_token"], "viewer"))
            self.assertIsNotNone(store.auth(second["session_token"], "viewer"))

    def test_older_import_does_not_replace_newer_friend_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(f"{directory}/hosted.sqlite3")
            bootstrapped = store.bootstrap("one", "collector")
            collector = store.auth(bootstrapped["collector_token"], "collector")
            store.ingest(collector["tenant_id"], collector["id"], [{"id": "usr_1", "displayName": "Fresh", "status": "active", "updatedAt": "2026-08-26T10:00:00+00:00"}], [])

            result = store.import_json(collector["tenant_id"], {"format": "vrchat-monitor-hosted-backup", "version": 1, "friends": [{"id": "usr_1", "display_name": "Stale", "status": "offline", "updated_at": "2026-08-26T11:00:00+02:00"}], "status_events": []})

            friend = store.data(collector["tenant_id"])["friends"][0]
            self.assertEqual(friend["display_name"], "Fresh")
            self.assertEqual(friend["status"], "active")
            self.assertEqual(result["friends"], 0)

            missing_time = store.import_json(collector["tenant_id"], {"format": "vrchat-monitor-hosted-backup", "version": 1, "friends": [{"id": "usr_1", "display_name": "Missing time", "status": "offline"}], "status_events": []})
            self.assertEqual(store.data(collector["tenant_id"])["friends"][0]["display_name"], "Fresh")
            self.assertEqual(missing_time["friends"], 0)

    def test_viewer_identity_contains_only_friendly_tenant_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(f"{directory}/hosted.sqlite3")
            bootstrapped = store.bootstrap("Alice 的监控", "collector")
            session = store.exchange_access_code(bootstrapped["access_code"])

            identity = store.viewer_identity(session["session_token"])

            self.assertEqual(identity, {"tenant_id": bootstrapped["tenant_id"], "name": "Alice 的监控", "avatar_url": ""})

    def test_tenants_cannot_read_each_other(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(f"{directory}/hosted.sqlite3")
            first = store.bootstrap("one", "collector")
            second = store.bootstrap("two", "collector")
            first_collector = store.auth(first["collector_token"], "collector")
            second_collector = store.auth(second["collector_token"], "collector")
            first_session = store.exchange_access_code(first["access_code"])
            second_session = store.exchange_access_code(second["access_code"])
            self.assertIsNotNone(first_session)
            self.assertIsNotNone(second_session)
            first_viewer = store.auth(first_session["session_token"], "viewer")
            second_viewer = store.auth(second_session["session_token"], "viewer")
            store.ingest(first_collector["tenant_id"], first_collector["id"], [{"id": "usr_1", "displayName": "Only one", "status": "active"}], [])
            store.ingest(second_collector["tenant_id"], second_collector["id"], [{"id": "usr_2", "displayName": "Only two", "status": "active"}], [])
            self.assertEqual([row["id"] for row in store.data(first_viewer["tenant_id"])["friends"]], ["usr_1"])
            self.assertEqual([row["id"] for row in store.data(second_viewer["tenant_id"])["friends"]], ["usr_2"])


if __name__ == "__main__":
    unittest.main()
