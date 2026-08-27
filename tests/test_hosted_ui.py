from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class HostedUiTests(unittest.TestCase):
    def test_login_page_uses_friendly_access_code_language(self):
        screens = (ROOT / "web/src/components/AuthScreens.tsx").read_text(encoding="utf-8")
        data_view = (ROOT / "web/src/views/DataView.tsx").read_text(encoding="utf-8")

        self.assertIn("打开你的监控面板", screens)
        self.assertIn("访问码只用于这个监控站点，不是 VRChat 密码", screens)
        self.assertIn("导入预览", data_view)
        self.assertNotIn("viewer token", screens.lower())
        self.assertNotIn("collector token", screens.lower())
        self.assertNotIn("RSA", screens)
        self.assertNotIn("AES", screens)

    def test_client_restores_and_clears_browser_session(self):
        script = (ROOT / "web/src/api.ts").read_text(encoding="utf-8")
        app = (ROOT / "web/src/App.tsx").read_text(encoding="utf-8")

        self.assertIn("credentials: 'same-origin'", script)
        self.assertIn("localStorage.removeItem", script)
        self.assertIn("request('/v1/me'", script)
        self.assertIn("request('/v1/logout'", script)
        self.assertNotIn("session_token", script)
        self.assertIn("<OfflineScreen", app)
        self.assertIn("<LoginScreen", app)

    def test_frontend_is_built_without_inline_code_or_source_maps(self):
        html_path = ROOT / "server/static/index.html"
        if not html_path.is_file():
            self.skipTest("run npm build before checking the production bundle")
        html = html_path.read_text(encoding="utf-8")
        self.assertIn('type="module"', html)
        self.assertNotIn("<style", html)
        self.assertNotIn("<script>", html)
        self.assertFalse(any((ROOT / "server/static").rglob("*.map")))


if __name__ == "__main__":
    unittest.main()
