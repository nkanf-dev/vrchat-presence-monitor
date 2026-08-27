import unittest

from scripts.watch_vrchat_tunnel import is_healthy, should_restart


class TunnelWatchdogTests(unittest.TestCase):
    def test_local_service_requires_success_or_redirect(self):
        self.assertTrue(is_healthy(200))
        self.assertTrue(is_healthy(302))
        self.assertFalse(is_healthy(0))
        self.assertFalse(is_healthy(530))

    def test_public_probe_allows_access_control_responses(self):
        self.assertTrue(is_healthy(200, allow_client_errors=True))
        self.assertTrue(is_healthy(403, allow_client_errors=True))
        self.assertFalse(is_healthy(530, allow_client_errors=True))

    def test_restart_needs_two_consecutive_failures(self):
        self.assertFalse(should_restart(0))
        self.assertFalse(should_restart(1))
        self.assertTrue(should_restart(2))


if __name__ == "__main__":
    unittest.main()
