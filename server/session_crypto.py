from __future__ import annotations

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SessionCipher:
    """Small versioned AEAD envelope for persisted VRChat cookies."""

    def __init__(self, key: bytes, key_version: int = 1):
        if len(key) != 32 or not 1 <= key_version <= 255:
            raise ValueError("invalid session encryption key")
        self._aead = AESGCM(key)
        self._version = key_version

    @staticmethod
    def _aad(tenant_id: str, vrchat_user_id: str) -> bytes:
        return f"presence-monitor|{tenant_id}|{vrchat_user_id}".encode("utf-8")

    def encrypt(self, tenant_id: str, vrchat_user_id: str, cookie: str) -> bytes:
        if not cookie:
            raise ValueError("empty VRChat session")
        nonce = os.urandom(12)
        encrypted = self._aead.encrypt(
            nonce,
            cookie.encode("utf-8"),
            self._aad(tenant_id, vrchat_user_id),
        )
        return bytes((self._version,)) + nonce + encrypted

    def decrypt(self, tenant_id: str, vrchat_user_id: str, envelope: bytes) -> str:
        raw = bytes(envelope or b"")
        if len(raw) < 30 or raw[0] != self._version:
            raise ValueError("invalid session envelope")
        try:
            plaintext = self._aead.decrypt(
                raw[1:13],
                raw[13:],
                self._aad(tenant_id, vrchat_user_id),
            )
            return plaintext.decode("utf-8")
        except (InvalidTag, UnicodeDecodeError) as error:
            raise ValueError("invalid session envelope") from error

    def encrypt_pending(self, pending_id: str, cookie: str) -> bytes:
        return self.encrypt("pending", pending_id, cookie)

    def decrypt_pending(self, pending_id: str, envelope: bytes) -> str:
        return self.decrypt("pending", pending_id, envelope)
