import json
import unittest

from vrchat_monitor.backup_io import decode_backup_upload, encode_backup_gzip


class BackupIoTests(unittest.TestCase):
    def test_gzip_backup_round_trips_and_is_deterministic(self):
        payload = {
            "format": "vrchat-monitor-backup",
            "version": 2,
            "friends": [{"id": "usr_1", "display_name": "测试"}],
            "status_events": [],
            "sync_runs": [],
            "raw_fetches": [],
        }
        first = encode_backup_gzip(payload)
        second = encode_backup_gzip(payload)

        self.assertTrue(first.startswith(b"\x1f\x8b"))
        self.assertEqual(first, second)
        self.assertEqual(decode_backup_upload(first), payload)

    def test_plain_legacy_json_remains_importable(self):
        payload = {"format": "vrchat-monitor-backup", "version": 1}
        raw = json.dumps(payload).encode("utf-8")
        self.assertEqual(decode_backup_upload(raw), payload)

    def test_expanded_size_limit_rejects_a_compression_bomb(self):
        compressed = encode_backup_gzip({"value": "x" * 4096})
        with self.assertRaisesRegex(ValueError, "解压后过大"):
            decode_backup_upload(compressed, max_compressed_bytes=1024, max_json_bytes=128)

    def test_corrupt_gzip_and_invalid_json_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "已损坏"):
            decode_backup_upload(b"\x1f\x8bbroken")
        with self.assertRaisesRegex(ValueError, "有效 JSON"):
            decode_backup_upload(b"{not-json")
        with self.assertRaisesRegex(ValueError, "重复字段"):
            decode_backup_upload(b'{"friends":[],"friends":[]}')


if __name__ == "__main__":
    unittest.main()
