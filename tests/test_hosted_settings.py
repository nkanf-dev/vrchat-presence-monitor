from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.settings import Settings


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


if __name__ == "__main__":
    unittest.main()
