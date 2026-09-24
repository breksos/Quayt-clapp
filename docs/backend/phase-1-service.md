# Phase 1 central service

This document records the Phase 1 foundation. Phase 2 configuration and runtime commands
are defined in `phase-2-authentication.md` and supersede the Phase 1 development defaults.

The Python service is a separate HTTPS/JSON authority boundary. Phase 1 provides only
liveness, readiness, an empty versioned API root, configuration validation, explicit
request-context types, and the common error envelope. It contains no login flow, tenant
selection, PostgreSQL schema, operational route, integration, scheduler, or mock provider.

## HTTP boundary

- `GET /health/live` reports that the process can serve requests.
- `GET /health/ready` reports that startup validation completed. Phase 1 has no database
  connection to probe; a database readiness check belongs with the first database work.
- `GET /api/v1` identifies the API and service versions.
- `GET /api/v1/openapi.json` publishes only the versioned API surface. Interactive docs
  are disabled.
- Errors use `{"error":{"code","message","request_id","details"?}}`. The server creates
  `X-Request-ID`; callers cannot choose it.

`TenantContext`, `ActorContext`, and `RequestContext` are immutable and require explicit,
non-nil UUID identities. They are not constructed from client-supplied tenant headers.
A later authentication layer must construct them from verified server-issued identity and
tenant-membership records.

## Configuration

`QUAYT_ENV` is mandatory and accepts only `development`, `test`, or `production`. There
is no default environment: an absent or blank value stops startup. Production startup
also requires all of:

- `QUAYT_DATABASE_URL`: a `postgresql://` or `postgresql+psycopg://` URL;
- `QUAYT_AUTH_ISSUER`: HTTPS issuer URL without embedded credentials;
- `QUAYT_AUTH_AUDIENCE`: non-empty service audience;
- `QUAYT_AUTH_JWKS_URL`: HTTPS JWKS URL without embedded credentials;
- `QUAYT_ALLOWED_ORIGINS`: comma-separated HTTPS origins, never `*`.

Development and test may omit these values because Phase 1 exposes no authenticated or
business operation. Omission does not create an authentication bypass, tenant, actor,
database, provider, or privileged context.

## Local commands

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
export QUAYT_ENV=development
python -m quayt_service
```

Install the lock before the editable package. `--no-deps` prevents the editable install
from resolving versions outside the verified dependency set.

The development server binds to `127.0.0.1:8080`. TLS termination and production process
management are intentionally deferred to the release-owned deployment workstream.
