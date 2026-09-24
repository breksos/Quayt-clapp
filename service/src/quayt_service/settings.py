"""Environment-backed service configuration with production safety gates."""

from __future__ import annotations

import base64
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlsplit


class Environment(str, Enum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class SettingsError(ValueError):
    """Raised before startup when configuration is incomplete or unsafe."""


def _optional(environ: Mapping[str, str], name: str) -> str | None:
    value = environ.get(name, "").strip()
    return value or None


def _https_url(name: str, value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise SettingsError(f"{name} must be an HTTPS URL without embedded credentials")


@dataclass(frozen=True)
class Settings:
    environment: Environment
    database_url: str | None
    auth_issuer: str | None
    auth_audience: str | None
    auth_jwks_url: str | None
    allowed_origins: tuple[str, ...]
    authorization_endpoint: str
    token_endpoint: str
    oidc_client_id: str
    oidc_scopes: tuple[str, ...]
    session_hash_key: bytes = field(repr=False)
    session_ttl_seconds: int = 2_592_000
    max_active_sessions_per_actor: int = 5
    service_name: str = "quayt-service"
    service_version: str = "0.1.0"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if environ is None else environ
        raw_environment = source.get("QUAYT_ENV", "").strip()
        if not raw_environment:
            raise SettingsError("QUAYT_ENV is required and must be set explicitly")
        try:
            environment = Environment(raw_environment)
        except ValueError as exc:
            choices = ", ".join(item.value for item in Environment)
            raise SettingsError(f"QUAYT_ENV must be one of: {choices}") from exc

        origins = tuple(
            origin.strip()
            for origin in source.get("QUAYT_ALLOWED_ORIGINS", "").split(",")
            if origin.strip()
        )
        required_names = (
            "QUAYT_DATABASE_URL",
            "QUAYT_AUTH_ISSUER",
            "QUAYT_AUTH_AUDIENCE",
            "QUAYT_AUTH_JWKS_URL",
            "QUAYT_AUTHORIZATION_ENDPOINT",
            "QUAYT_TOKEN_ENDPOINT",
            "QUAYT_OIDC_CLIENT_ID",
            "QUAYT_OIDC_SCOPES",
            "QUAYT_SESSION_HASH_KEY",
        )
        missing = [name for name in required_names if _optional(source, name) is None]
        if missing:
            raise SettingsError("missing service settings: " + ", ".join(sorted(missing)))
        encoded_key = source["QUAYT_SESSION_HASH_KEY"].strip()
        try:
            session_hash_key = base64.urlsafe_b64decode(encoded_key + "=" * (-len(encoded_key) % 4))
        except Exception as exc:
            raise SettingsError("QUAYT_SESSION_HASH_KEY must be URL-safe base64") from exc
        if len(session_hash_key) < 32:
            raise SettingsError("QUAYT_SESSION_HASH_KEY must decode to at least 32 bytes")
        raw_session_cap = source.get("QUAYT_MAX_ACTIVE_SESSIONS_PER_ACTOR", "5").strip()
        try:
            session_cap = int(raw_session_cap)
        except ValueError as exc:
            raise SettingsError("QUAYT_MAX_ACTIVE_SESSIONS_PER_ACTOR must be an integer") from exc
        if not 1 <= session_cap <= 20:
            raise SettingsError("QUAYT_MAX_ACTIVE_SESSIONS_PER_ACTOR must be between 1 and 20")
        settings = cls(
            environment=environment,
            database_url=_optional(source, "QUAYT_DATABASE_URL"),
            auth_issuer=_optional(source, "QUAYT_AUTH_ISSUER"),
            auth_audience=_optional(source, "QUAYT_AUTH_AUDIENCE"),
            auth_jwks_url=_optional(source, "QUAYT_AUTH_JWKS_URL"),
            allowed_origins=origins,
            authorization_endpoint=source["QUAYT_AUTHORIZATION_ENDPOINT"].strip(),
            token_endpoint=source["QUAYT_TOKEN_ENDPOINT"].strip(),
            oidc_client_id=source["QUAYT_OIDC_CLIENT_ID"].strip(),
            oidc_scopes=tuple(source["QUAYT_OIDC_SCOPES"].split()),
            session_hash_key=session_hash_key,
            max_active_sessions_per_actor=session_cap,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.database_url is None:
            raise SettingsError("QUAYT_DATABASE_URL is required")
        database = urlsplit(self.database_url)
        if database.scheme not in {"postgresql", "postgresql+psycopg"} or not database.hostname:
            raise SettingsError("QUAYT_DATABASE_URL must identify a PostgreSQL server")

        if self.auth_issuer is None or self.auth_jwks_url is None or self.auth_audience is None:
            raise SettingsError("OIDC issuer, audience, and JWKS URL are required")
        if not self.oidc_client_id or not self.oidc_scopes:
            raise SettingsError("OIDC client ID and scopes are required")
        if len(self.session_hash_key) < 32:
            raise SettingsError("QUAYT_SESSION_HASH_KEY must contain at least 32 bytes")
        if not 1 <= self.max_active_sessions_per_actor <= 20:
            raise SettingsError("active session cap must be between 1 and 20")
        endpoints = {
            "QUAYT_AUTH_ISSUER": self.auth_issuer,
            "QUAYT_AUTH_JWKS_URL": self.auth_jwks_url,
            "QUAYT_AUTHORIZATION_ENDPOINT": self.authorization_endpoint,
            "QUAYT_TOKEN_ENDPOINT": self.token_endpoint,
        }
        if self.environment is Environment.PRODUCTION:
            for name, value in endpoints.items():
                _https_url(name, value)
        else:
            for name, value in endpoints.items():
                parsed = urlsplit(value)
                if (
                    parsed.scheme == "https"
                    and parsed.netloc
                    and not parsed.username
                    and not parsed.password
                ):
                    continue
                if (
                    parsed.scheme != "http"
                    or parsed.hostname not in {"127.0.0.1", "localhost"}
                    or parsed.username
                    or parsed.password
                ):
                    raise SettingsError(f"{name} must use HTTPS or loopback HTTP")

        for origin in self.allowed_origins:
            if origin == "*":
                raise SettingsError("QUAYT_ALLOWED_ORIGINS cannot contain '*' in production")
            if self.environment is Environment.PRODUCTION:
                _https_url("QUAYT_ALLOWED_ORIGINS", origin)
            parsed_origin = urlsplit(origin)
            if self.environment is not Environment.PRODUCTION and not (
                parsed_origin.scheme == "https"
                or (
                    parsed_origin.scheme == "http"
                    and parsed_origin.hostname in {"127.0.0.1", "localhost"}
                )
            ):
                raise SettingsError("QUAYT_ALLOWED_ORIGINS must use HTTPS or loopback HTTP")
            if parsed_origin.path not in {"", "/"} or parsed_origin.query or parsed_origin.fragment:
                raise SettingsError(
                    "QUAYT_ALLOWED_ORIGINS entries must be origins, not URLs with paths"
                )
