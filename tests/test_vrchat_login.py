from __future__ import annotations

import unittest

from server.session_crypto import SessionCipher
from server.vrchat_auth import MemoryCredentialStore, VRChatAuthService
from vrchat_monitor.vrchat import VRChatLoginResult


class _EmailOtpClient:
    completed_with: tuple[str, str, str] | None = None

    def __init__(self, store: MemoryCredentialStore):
        self.store = store

    def begin_login(self, username: str, password: str) -> VRChatLoginResult:
        return VRChatLoginResult(True, "auth=pending", ("emailOtp",))

    def complete_2fa(self, cookie: str, code: str, method: str) -> VRChatLoginResult:
        type(self).completed_with = (cookie, code, method)
        return VRChatLoginResult(False, "auth=complete", user={"id": "usr_test"})


class HostedVRChatLoginTests(unittest.TestCase):
    def test_email_otp_challenge_uses_email_verification_endpoint(self) -> None:
        service = VRChatAuthService(
            SessionCipher(b"x" * 32),
            client_factory=_EmailOtpClient,
        )
        pending_id, result = service.begin("gera", "correct")
        self.assertTrue(result.requires_2fa)
        self.assertIsNotNone(pending_id)

        completed = service.complete(str(pending_id), "123456")

        self.assertEqual(_EmailOtpClient.completed_with, ("auth=pending", "123456", "emailOtp"))
        self.assertEqual(completed.user, {"id": "usr_test"})


if __name__ == "__main__":
    unittest.main()
