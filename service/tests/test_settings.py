import pytest

from quayt_service.settings import Environment, Settings, SettingsError


@pytest.mark.parametrize("environ", [{}, {"QUAYT_ENV": ""}, {"QUAYT_ENV": "   "}])
def test_environment_must_be_explicit(environ: dict[str, str]) -> None:
    with pytest.raises(SettingsError, match="QUAYT_ENV is required"):
        Settings.from_env(environ)


def test_production_rejects_missing_configuration() -> None:
    with pytest.raises(SettingsError) as error:
        Settings.from_env({"QUAYT_ENV": "production"})

    message = str(error.value)
    assert "QUAYT_DATABASE_URL" in message
    assert "QUAYT_AUTH_ISSUER" in message
    assert "QUAYT_AUTH_AUDIENCE" in message
    assert "QUAYT_AUTH_JWKS_URL" in message
    assert "QUAYT_ALLOWED_ORIGINS" in message


def test_production_rejects_insecure_identity_urls_and_wildcard_cors() -> None:
    base = {
        "QUAYT_ENV": "production",
        "QUAYT_DATABASE_URL": "postgresql+psycopg://db/quayt",
        "QUAYT_AUTH_ISSUER": "http://identity.example.test",
        "QUAYT_AUTH_AUDIENCE": "quayt-service",
        "QUAYT_AUTH_JWKS_URL": "https://identity.example.test/.well-known/jwks.json",
        "QUAYT_ALLOWED_ORIGINS": "https://app.example.test",
    }
    with pytest.raises(SettingsError, match="QUAYT_AUTH_ISSUER must be an HTTPS URL"):
        Settings.from_env(base)

    base["QUAYT_AUTH_ISSUER"] = "https://identity.example.test"
    base["QUAYT_ALLOWED_ORIGINS"] = "*"
    with pytest.raises(SettingsError, match="cannot contain"):
        Settings.from_env(base)


def test_development_has_no_implicit_identity_or_database() -> None:
    settings = Settings.from_env({"QUAYT_ENV": "development"})

    assert settings.environment is Environment.DEVELOPMENT
    assert settings.database_url is None
    assert settings.auth_issuer is None
    assert settings.auth_audience is None
    assert settings.auth_jwks_url is None
    assert settings.allowed_origins == ()
