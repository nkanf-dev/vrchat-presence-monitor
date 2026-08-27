from __future__ import annotations

import ssl
import unittest
from pathlib import Path

from vrchat_monitor.http_assets import static_asset_for_request
from vrchat_monitor.vrchat import secure_tls_context


class LocalHttpSecurityTests(unittest.TestCase):
    def test_static_assets_use_an_explicit_allowlist(self):
        root = Path("/application/static")

        self.assertEqual(
            static_asset_for_request(root, "/app.js?cache=1"),
            (root / "app.js", "text/javascript; charset=utf-8"),
        )
        for target in (
            "/../session.cookie",
            "/%2e%2e/session.cookie",
            "/nested/asset.js",
            "/app.js%0d%0aX-Injected:true",
        ):
            with self.subTest(target=target):
                self.assertIsNone(static_asset_for_request(root, target))

    def test_pipeline_tls_requires_tls_1_2_or_newer(self):
        context = secure_tls_context()

        self.assertGreaterEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)

    def test_local_server_has_no_user_controlled_image_proxy(self):
        root = Path(__file__).resolve().parents[1]
        self.assertNotIn("/api/world-image", (root / "vrchat_monitor/app.py").read_text())
        self.assertNotIn("/api/world-image", (root / "vrchat_monitor/static/app.js").read_text())

    def test_local_exports_report_failures_without_leaving_the_dashboard(self):
        root = Path(__file__).resolve().parents[1]
        app = (root / "vrchat_monitor/static/app.js").read_text()
        page = (root / "vrchat_monitor/static/index.html").read_text()

        self.assertNotIn("window.location='/api/export", app)
        self.assertIn("downloadExport('/api/export.json'", app)
        self.assertIn("toast(`导出失败：${error.message}`, 120000, true)", app)
        self.assertIn('id="toast" role="alert" aria-live="assertive"', page)


if __name__ == "__main__":
    unittest.main()
