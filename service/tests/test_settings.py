import base64

import pytest
from conftest import settings_env

from quayt_service.settings import Environment, Settings, SettingsError


@pytest.mark.parametrize("environ", [{}, {"QUAYT_ENV": ""}, {"QUAYT_ENV": "   "}])
def test_environment_must_be_explicit(environ: dict[str, str]) -> None:
    with pytest.raises(SettingsError, match="QUAYT_ENV is required"):
        Settings.from_env(environ)


def test_all_modes_require_database_oidc_and_hash_configuration() -> None:
    with pytest.raises(SettingsError) as error:
        Settings.from_env({"QUAYT_ENV": "development"})
    message = str(error.value)
    assert "QUAYT_DATABASE_URL" in message
    assert "QUAYT_AUTH_JWKS_URL" in message
    assert "QUAYT_SESSION_HASH_KEY" in message


def test_hash_key_must_have_256_bits() -> None:
    encoded = base64.urlsafe_b64encode(b"short").decode()
    with pytest.raises(SettingsError, match="at least 32 bytes"):
        Settings.from_env(settings_env(QUAYT_SESSION_HASH_KEY=encoded))


@pytest.mark.parametrize("value", ["0", "21", "not-an-integer"])
def test_active_session_cap_is_conservative(value: str) -> None:
    with pytest.raises(SettingsError, match="QUAYT_MAX_ACTIVE_SESSIONS_PER_ACTOR"):
        Settings.from_env(settings_env(QUAYT_MAX_ACTIVE_SESSIONS_PER_ACTOR=value))


def test_development_allows_only_loopback_http() -> None:
    with pytest.raises(SettingsError, match="HTTPS or loopback HTTP"):
        Settings.from_env(settings_env(QUAYT_AUTH_ISSUER="http://identity.example.test"))


def test_production_rejects_insecure_identity_urls_and_wildcard_cors() -> None:
    values = settings_env(
        QUAYT_ENV="production",
        QUAYT_AUTH_ISSUER="http://127.0.0.1:9000",
        QUAYT_ALLOWED_ORIGINS="https://app.example.test",
    )
    with pytest.raises(SettingsError, match="QUAYT_AUTH_ISSUER must be an HTTPS URL"):
        Settings.from_env(values)

    for name in (
        "QUAYT_AUTH_ISSUER",
        "QUAYT_AUTH_JWKS_URL",
        "QUAYT_AUTHORIZATION_ENDPOINT",
        "QUAYT_TOKEN_ENDPOINT",
    ):
        values[name] = f"https://identity.example.test/{name.lower()}"
    values["QUAYT_ALLOWED_ORIGINS"] = "*"
    with pytest.raises(SettingsError, match="cannot contain"):
        Settings.from_env(values)


def test_explicit_test_configuration_is_valid() -> None:
    settings = Settings.from_env(settings_env())
    assert settings.environment is Environment.TEST
    assert settings.oidc_scopes == ("openid", "profile")
    assert settings.session_hash_key == b"k" * 32
