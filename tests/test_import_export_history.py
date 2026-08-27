import json
import tempfile
import unittest

from vrchat_monitor.db import Database


class ImportExportHistoryTests(unittest.TestCase):
    def test_history_is_searchable_and_backup_is_merge_only(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(f"{directory}/monitor.sqlite3")
            db.upsert_friends([{"id": "usr_1", "username": "alice", "displayName": "Alice", "status": "active", "location": "wrld_test"}], source="test")
            db.upsert_friends([{"id": "usr_1", "username": "alice", "displayName": "Alice", "status": "offline", "location": "offline"}], source="test")
            page = db.history_page(limit=1, query="alice")
            self.assertEqual(page["total"], 2)
            self.assertEqual(len(page["items"]), 1)
            backup = db.json_export()
            self.assertEqual(backup["format"], "vrchat-monitor-backup")
            self.assertNotIn("auth_cookie", json.dumps(backup))
            with tempfile.TemporaryDirectory() as second_directory:
                restored = Database(f"{second_directory}/monitor.sqlite3")
                result = restored.json_import(backup)
                self.assertEqual(result["status_events"], 2)
                self.assertEqual(restored.history_page()["total"], 2)
                self.assertEqual(restored.json_import(backup)["status_events"], 0)


if __name__ == "__main__":
    unittest.main()
