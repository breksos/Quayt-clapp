"""Strict OIDC access-token verification with no development bypass."""

from __future__ import annotations

from typing import Any, Protocol

import jwt
from jwt import PyJWKClient

from quayt_service.domain import VerifiedIdentity
from quayt_service.settings import Settings


class TokenVerificationError(ValueError):
    pass


class OidcVerifier(Protocol):
    def verify(self, access_token: str) -> VerifiedIdentity: ...

    def ready(self) -> bool: ...


class SigningKeyResolver(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any: ...

    def get_signing_keys(self) -> list[Any]: ...


class JwtOidcVerifier:
    allowed_algorithms = ("RS256",)

    def __init__(
        self,
        settings: Settings,
        key_resolver: SigningKeyResolver | None = None,
    ) -> None:
        assert settings.auth_jwks_url is not None
        assert settings.auth_issuer is not None
        assert settings.auth_audience is not None
        self._issuer = settings.auth_issuer
        self._audience = settings.auth_audience
        self._keys: SigningKeyResolver = key_resolver or PyJWKClient(
            settings.auth_jwks_url,
            cache_jwk_set=True,
            lifespan=300,
            timeout=5,
        )

    def verify(self, access_token: str) -> VerifiedIdentity:
        if not access_token or len(access_token) > 16_384:
            raise TokenVerificationError("access token rejected")
        try:
            header = jwt.get_unverified_header(access_token)
            if header.get("alg") not in self.allowed_algorithms or not header.get("kid"):
                raise TokenVerificationError("access token rejected")
            signing_key = self._keys.get_signing_key_from_jwt(access_token)
            claims = jwt.decode(
                access_token,
                signing_key.key,
                algorithms=list(self.allowed_algorithms),
                audience=self._audience,
                issuer=self._issuer,
                options={
                    "require": ["iss", "aud", "exp", "iat", "sub"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iat": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
            )
        except TokenVerificationError:
            raise
        except Exception as exc:
            raise TokenVerificationError("access token rejected") from exc

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise TokenVerificationError("access token rejected")
        name = claims.get("name")
        display_name = name.strip()[:255] if isinstance(name, str) and name.strip() else None
        return VerifiedIdentity(self._issuer, subject, display_name)

    def ready(self) -> bool:
        try:
            return bool(self._keys.get_signing_keys())
        except Exception:
            return False
