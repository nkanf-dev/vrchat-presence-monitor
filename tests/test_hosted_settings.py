from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.settings import DEFAULT_MAX_IMPORT_BYTES, Settings


class HostedSettingsTests(unittest.TestCase):
    def test_bootstrap_secret_can_be_read_from_a_container_secret_file(self):
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "bootstrap"
            secret.write_text("file-secret\n", encoding="utf-8")
            with patch.dict(os.environ, {"BOOTSTRAP_TOKEN_FILE": str(secret)}, clear=True):
                settings = Settings.from_env()
        self.assertEqual(settings.bootstrap_token, "file-secret")

    def test_ambiguous_secret_configuration_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "bootstrap"
            secret.write_text("file-secret", encoding="utf-8")
            environment = {"BOOTSTRAP_TOKEN": "env-secret", "BOOTSTRAP_TOKEN_FILE": str(secret)}
            with patch.dict(os.environ, environment, clear=True):
                with self.assertRaisesRegex(ValueError, "set only one"):
                    Settings.from_env()

    def test_import_rate_limit_is_configurable_and_bounded(self):
        with patch.dict(
            os.environ,
            {
                "BOOTSTRAP_TOKEN": "secret",
                "IMPORT_REQUESTS": "7",
                "IMPORT_WINDOW_SECONDS": "90",
            },
            clear=True,
        ):
            settings = Settings.from_env()
        self.assertEqual(settings.import_requests, 7)
        self.assertEqual(settings.import_window_seconds, 90)

        with self.assertRaisesRegex(ValueError, "IMPORT_REQUESTS"):
            Settings(
                data_dir=Path("/tmp"),
                static_dir=Path("/tmp"),
                bootstrap_token="secret",
                import_requests=0,
            )

    def test_import_capacity_is_bounded_by_the_container_memory_budget(self):
        with patch.dict(os.environ, {"BOOTSTRAP_TOKEN": "secret"}, clear=True):
            defaults = Settings.from_env()
        self.assertEqual(defaults.max_import_bytes, DEFAULT_MAX_IMPORT_BYTES)
        self.assertGreater(
            defaults.max_import_expanded_bytes,
            defaults.max_import_bytes,
        )
        self.assertGreater(
            defaults.max_source_expanded_bytes,
            defaults.max_import_expanded_bytes,
        )

        with self.assertRaisesRegex(ValueError, "MAX_IMPORT_BYTES"):
            Settings(
                data_dir=Path("/tmp"),
                static_dir=Path("/tmp"),
                bootstrap_token="secret",
                max_import_bytes=65 * 1024 * 1024,
            )
        with self.assertRaisesRegex(ValueError, "MAX_IMPORT_EXPANDED_BYTES"):
            Settings(
                data_dir=Path("/tmp"),
                static_dir=Path("/tmp"),
                bootstrap_token="secret",
                max_import_bytes=32 * 1024 * 1024,
                max_import_expanded_bytes=65 * 1024 * 1024,
            )
        with self.assertRaisesRegex(ValueError, "MAX_IMPORT_EXPANDED_BYTES"):
            Settings(
                data_dir=Path("/tmp"),
                static_dir=Path("/tmp"),
                bootstrap_token="secret",
                max_import_bytes=2 * 1024,
                max_import_expanded_bytes=1024,
            )
        with self.assertRaisesRegex(ValueError, "MAX_SOURCE_EXPANDED_BYTES"):
            Settings(
                data_dir=Path("/tmp"),
                static_dir=Path("/tmp"),
                bootstrap_token="secret",
                max_source_expanded_bytes=32 * 1024 * 1024,
            )
        supported = Settings(
            data_dir=Path("/tmp"),
            static_dir=Path("/tmp"),
            bootstrap_token="secret",
            max_import_bytes=64 * 1024 * 1024,
            max_import_expanded_bytes=64 * 1024 * 1024,
            max_source_expanded_bytes=512 * 1024 * 1024,
        )
        self.assertEqual(supported.max_source_expanded_bytes, 512 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
