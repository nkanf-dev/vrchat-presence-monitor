import tempfile
import unittest
from pathlib import Path

from vrchat_monitor.db import Database


class RawFetchDatabaseTests(unittest.TestCase):
    def test_raw_response_is_append_only_and_retrievable(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / "monitor.sqlite3"))
            payload = b'{"presence":{"world":"offline"}}'
            db.record_raw_fetch("GET", "/auth/user", 200, "application/json", payload)
            db.record_raw_fetch("GET", "/auth/user", 503, "text/plain", b"temporary", "upstream")

            rows = db.raw_fetches(10)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["status_code"], 503)
            self.assertEqual(rows[0]["body"], "temporary")
            self.assertEqual(rows[1]["body"], payload.decode())
            self.assertEqual(db.raw_fetch(rows[1]["id"])["body"], payload.decode())


if __name__ == "__main__":
    unittest.main()
