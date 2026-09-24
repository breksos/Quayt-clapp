from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from quayt_service.oidc import JwtOidcVerifier, TokenVerificationError
from quayt_service.settings import Settings


class StaticKeys:
    def __init__(self, public_key: object, *, ready: bool = True) -> None:
        self.public_key = public_key
        self.ready_value = ready

    def get_signing_key_from_jwt(self, token: str) -> object:
        del token
        return SimpleNamespace(key=self.public_key)

    def get_signing_keys(self) -> list[object]:
        return [self.public_key] if self.ready_value else []


def claims(**overrides: object) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    values: dict[str, object] = {
        "iss": "http://127.0.0.1:9000",
        "aud": "quayt-service",
        "sub": "subject-1",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "name": "Operator",
    }
    values.update(overrides)
    return values


def signed_token(private_key: object, values: dict[str, object]) -> str:
    return jwt.encode(values, private_key, algorithm="RS256", headers={"kid": "test-key"})


def test_strict_oidc_validation(settings: Settings) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = JwtOidcVerifier(settings, StaticKeys(key.public_key()))

    identity = verifier.verify(signed_token(key, claims()))
    assert identity.subject == "subject-1"
    assert identity.display_name == "Operator"
    assert verifier.ready()


@pytest.mark.parametrize(
    "overrides",
    [
        {"exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
        {"iss": "https://wrong.example"},
        {"aud": "wrong-audience"},
        {"sub": ""},
    ],
)
def test_oidc_rejects_invalid_claims(settings: Settings, overrides: dict[str, object]) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = JwtOidcVerifier(settings, StaticKeys(key.public_key()))
    with pytest.raises(TokenVerificationError, match="access token rejected"):
        verifier.verify(signed_token(key, claims(**overrides)))


def test_oidc_rejects_wrong_signature_algorithm_and_malformed_token(settings: Settings) -> None:
    trusted = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = JwtOidcVerifier(settings, StaticKeys(trusted.public_key()))

    wrong_signature = signed_token(attacker, claims())
    disallowed = jwt.encode(claims(), "a" * 32, algorithm="HS256", headers={"kid": "x"})
    for token in (wrong_signature, disallowed, "not-a-jwt"):
        with pytest.raises(TokenVerificationError, match="access token rejected"):
            verifier.verify(token)
