"""Credential generation and keyed one-way storage."""

from __future__ import annotations

import base64
import hmac
from uuid import UUID


class CredentialHasher:
    def __init__(self, key: bytes) -> None:
        if len(key) < 32:
            raise ValueError("credential hash key must contain at least 32 bytes")
        self._key = key

    def derive_credential(self, session_id: UUID, token_digest: bytes) -> str:
        material = hmac.digest(
            self._key,
            b"quayt-session-credential-v1\0" + session_id.bytes + token_digest,
            "sha256",
        )
        secret = base64.urlsafe_b64encode(material).decode().rstrip("=")
        return f"qyt_{session_id.hex}_{secret}"

    def parse_session_id(self, credential: str) -> UUID | None:
        parts = credential.split("_", 2)
        if len(parts) != 3 or parts[0] != "qyt" or len(parts[1]) != 32 or len(credential) > 160:
            return None
        try:
            return UUID(hex=parts[1])
        except ValueError:
            return None

    def credential_hash(self, credential: str) -> bytes:
        return hmac.digest(self._key, b"credential\0" + credential.encode(), "sha256")

    def token_digest(self, access_token: str) -> bytes:
        return hmac.digest(self._key, b"oidc-token\0" + access_token.encode(), "sha256")

    @staticmethod
    def matches(left: bytes, right: bytes) -> bool:
        return hmac.compare_digest(left, right)
