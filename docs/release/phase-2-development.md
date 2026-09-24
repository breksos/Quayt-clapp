# Phase 2 development and CI gates

Phase 2 is development-only. These commands do not package, sign, upload, deploy, or
publish a Quayt artifact. The service and PostgreSQL remain separate from the clapp.

## Local development

Start from the repository root with the `clappkit` submodule initialized. The compose
file exposes PostgreSQL only at `127.0.0.1:54329`; its database password is a
development-only placeholder, never a production credential.

```sh
cp .env.example .env
# Replace every REPLACE_WITH_ value with loopback development-provider metadata.
# Generate QUAYT_SESSION_HASH_KEY using the command in .env.example.
docker compose -f compose.dev.yml up -d postgres
python3.11 -m venv service/.venv
service/.venv/bin/python -m pip install --upgrade pip==25.0.1
service/.venv/bin/python -m pip install -r service/requirements.lock
service/.venv/bin/python -m pip install --no-deps -e './service[dev]'
set -a; . ./.env; set +a
(cd service && ../service/.venv/bin/alembic upgrade head)
(cd service && ../service/.venv/bin/alembic check)
service/.venv/bin/quayt-service
```

The service binds to `127.0.0.1:8080`. It fails closed until all OIDC configuration is
valid. The template supplies no identity-provider implementation, access token, refresh
token, client secret, or usable session-hash key.

Run the local gates from the repository root:

```sh
npm ci
npm run validate:manifest
# Clatch has no trusted CI installer; run locally with a trusted installation.
clatch validate .
python3 qa/phase2_acceptance.py
npm run build:web
cargo fetch --manifest-path src-tauri/Cargo.toml --locked
cargo fmt --manifest-path src-tauri/Cargo.toml --check
cargo build --manifest-path src-tauri/Cargo.toml --locked
cargo test --manifest-path src-tauri/Cargo.toml --locked
set -a; . ./.env; set +a
(cd service && ../service/.venv/bin/ruff format --check .)
(cd service && ../service/.venv/bin/ruff check .)
(cd service && ../service/.venv/bin/mypy)
(cd service && ../service/.venv/bin/pytest)
```

## CI

GitHub Actions pins checkout, Node 22.14.0, Rust 1.85.1, Python 3.11.11, and pip
25.0.1. The service job creates a clean PostgreSQL 17.5 database, applies the migration
head, runs `alembic check` for model/schema drift, then executes the service test suite.
The Phase 2 acceptance job is dependency-free and verifies the manifest boundary, lock,
migration baseline, and loopback-only development configuration. The repository manifest
validator runs in CI; `clatch validate .` remains a required local gate.

## Supply-chain record

The Phase 1 lock inputs remain required: `package-lock.json`, `src-tauri/Cargo.lock`,
the ClappKit gitlink, and `service/requirements.lock`. The CI actions and language
toolchains are pinned. Phase 2 adds the PostgreSQL `17.5-bookworm` CI/development image;
the image tag must be resolved and recorded by digest before any release candidate is
considered reproducible. The Rust Linux job also installs distribution packages from the
runner apt repositories, which are not content-pinned. No Clatch installer is trusted in
CI, so the authoritative Clatch validation remains local.
