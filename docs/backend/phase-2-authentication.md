# Phase 2 authentication service

The central service verifies provider access tokens only when a device session is created
or refreshed. Normal API requests use a server-issued opaque Quayt session credential.
PostgreSQL stores its keyed HMAC-SHA-256 hash, never the credential or provider token.

## API contract

- `GET /api/v1/auth/config` returns issuer, native-client endpoints, client ID, scopes,
  PKCE `S256`, and the loopback host. It contains no secret.
- `POST /api/v1/session` accepts `{client_instance_id}` with an OIDC bearer token and
  returns the session projection plus a one-time `session_credential`.
- `GET /api/v1/session` uses `Authorization: Session <credential>` and is read-only.
- `POST /api/v1/session/tenant` accepts `{tenant_id, expected_session_version}`. The
  server selects only an active membership and returns no credential.
- `POST /api/v1/session/refresh` accepts the refreshed OIDC bearer token and the current
  credential in `X-Quayt-Session-Credential`. Rotation is atomic. Reuse of a rotated
  credential revokes the complete device-session family.
- `POST /api/v1/session/logout` uses the session authorization header, revokes the whole
  family, and returns `204` idempotently.

Authenticated failures use generic `authentication_failed`, `tenant_access_denied`, or
`session_conflict` envelopes. Tenant selection never defaults from membership count.
`require_request_context` rechecks the session and active selected membership before it
constructs immutable actor and tenant context for future domain routes.

## PostgreSQL

The baseline migration creates `actors`, `tenants`, `memberships`, `device_sessions`,
`credential_generations`, and append-only `auth_audit`. Apply it from `service/`:

```sh
QUAYT_DATABASE_URL='postgresql+psycopg://…' alembic upgrade head
```

Readiness returns `200` only when PostgreSQL is reachable at migration head and the JWKS
resolver has at least one usable signing key. Liveness remains process-only.

## Required configuration

Every environment requires `QUAYT_ENV`, `QUAYT_DATABASE_URL`, `QUAYT_AUTH_ISSUER`,
`QUAYT_AUTH_AUDIENCE`, `QUAYT_AUTH_JWKS_URL`, `QUAYT_AUTHORIZATION_ENDPOINT`,
`QUAYT_TOKEN_ENDPOINT`, `QUAYT_OIDC_CLIENT_ID`, `QUAYT_OIDC_SCOPES`, and
`QUAYT_SESSION_HASH_KEY`. The hash key is URL-safe base64 encoding of at least 32 random
bytes. Production OIDC URLs must use HTTPS. Development and test permit plaintext HTTP
only on `127.0.0.1` or `localhost`. There is no environment-selectable verifier, mock
provider, default tenant, or authentication bypass.

## Local verification

Run from `service/` with Python 3.9 or newer:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
ruff check .
ruff format --check .
mypy src
pytest
```

Tests inject verifier and repository fixtures directly into the application factory.
Runtime configuration cannot select them.

