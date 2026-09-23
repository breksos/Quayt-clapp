"""Environment-backed service configuration with production safety gates."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
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
        settings = cls(
            environment=environment,
            database_url=_optional(source, "QUAYT_DATABASE_URL"),
            auth_issuer=_optional(source, "QUAYT_AUTH_ISSUER"),
            auth_audience=_optional(source, "QUAYT_AUTH_AUDIENCE"),
            auth_jwks_url=_optional(source, "QUAYT_AUTH_JWKS_URL"),
            allowed_origins=origins,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.environment is not Environment.PRODUCTION:
            return

        required = {
            "QUAYT_DATABASE_URL": self.database_url,
            "QUAYT_AUTH_ISSUER": self.auth_issuer,
            "QUAYT_AUTH_AUDIENCE": self.auth_audience,
            "QUAYT_AUTH_JWKS_URL": self.auth_jwks_url,
        }
        missing = [name for name, value in required.items() if value is None]
        if not self.allowed_origins:
            missing.append("QUAYT_ALLOWED_ORIGINS")
        if missing:
            raise SettingsError("missing production settings: " + ", ".join(sorted(missing)))

        assert self.database_url is not None
        database = urlsplit(self.database_url)
        if database.scheme not in {"postgresql", "postgresql+psycopg"} or not database.hostname:
            raise SettingsError("QUAYT_DATABASE_URL must identify a PostgreSQL server")

        assert self.auth_issuer is not None
        assert self.auth_jwks_url is not None
        _https_url("QUAYT_AUTH_ISSUER", self.auth_issuer)
        _https_url("QUAYT_AUTH_JWKS_URL", self.auth_jwks_url)

        for origin in self.allowed_origins:
            if origin == "*":
                raise SettingsError("QUAYT_ALLOWED_ORIGINS cannot contain '*' in production")
            _https_url("QUAYT_ALLOWED_ORIGINS", origin)
            parsed_origin = urlsplit(origin)
            if parsed_origin.path not in {"", "/"} or parsed_origin.query or parsed_origin.fragment:
                raise SettingsError(
                    "QUAYT_ALLOWED_ORIGINS entries must be origins, not URLs with paths"
                )
