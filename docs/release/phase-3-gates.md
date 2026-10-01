# Phase 3 release gate

Phase 3 remains development-only. No package, signature, deployment, publication, or
production change is authorized by this gate. The retained service is separate from the
ClappKit binary and PostgreSQL is a CI/local development dependency only.

## Required CI coverage

The pinned workflow in `.github/workflows/build.yml` covers the gate as follows:

- The manifest job runs `npm ci` and `npm run validate:manifest`.
- The acceptance job runs `python3 qa/phase1_acceptance.py`,
  `python3 qa/phase2_acceptance.py`, and `python3 qa/phase3_acceptance.py` from the
  repository root.
- The web job runs `npm ci` and `npm run build:web`.
- The Rust job runs locked fetch, format, build, and tests on Rust 1.85.1.
- The service job starts PostgreSQL 17.5, installs `service/requirements.lock`, runs
  `alembic upgrade head` on a clean database, runs `alembic check` for schema drift, and
  runs the complete service test suite. With the CI database URL set, the PostgreSQL
  vessel-call isolation and deterministic-seed tests execute instead of skipping.

The Phase 3 service tests cover tenant-scoped list/detail/summary/filter behavior,
cross-tenant denial, bounded inputs, deterministic seed behavior, and the non-development
seed refusal. Rust tests cover the five-command CLI, secret containment, and exclusion of
vessel-call data from the agent projection. The web build is the frontend compile and
bundle gate; no fake operational data is packaged by the release workflow.

## Local evidence commands

Run these from the repository root with the `clappkit` submodule initialized:

```sh
npm run validate:manifest
clatch validate .                         # trusted local Clatch installation required
python3 qa/phase1_acceptance.py
python3 qa/phase2_acceptance.py
python3 qa/phase3_acceptance.py
npm run build:web
cargo fetch --manifest-path src-tauri/Cargo.toml --locked
cargo fmt --manifest-path src-tauri/Cargo.toml --check
cargo test --manifest-path src-tauri/Cargo.toml --locked
(cd service && ../service/.venv/bin/alembic upgrade head)
(cd service && ../service/.venv/bin/alembic check)
(cd service && ../service/.venv/bin/pytest)
```

The manifest and Rust CLI must contain exactly `status`, `show`, `hide`, `quit`, and
`ping`, in that order. `connector.cliBin` remains `bin/quayt`; `connector.signals` remains
empty. The current ClappKit gitlink is pinned to `2cde16926c6a3aea276d18dccadf32bc4e4fb80c`.
The reproducible dependency inputs are `package-lock.json`, `src-tauri/Cargo.lock`,
`service/requirements.lock`, and the ClappKit gitlink.

## Packaging, rollback, and evidence limits

The manifest packaging inputs are present: `clatch.json`, `assets/icon.png`, and the staged
`bin/quayt` path expected by the manifest. `pkg/` and `*.clapp` remain derived and ignored;
this Phase 3 gate does not create or publish a depot. A rollback can therefore only mean
keeping the previous development checkout and database snapshot available; no production
rollback mechanism is being exercised or claimed here.

Evidence is incomplete for the environment-bound PostgreSQL checks. The current workstation
has no Docker runtime, so PostgreSQL `alembic upgrade head`, `alembic check`, and the live
database vessel-call test cannot be executed locally. The trusted Clatch executable is
available at `/Users/berk/.clatch/bin/clatch`; local `clatch validate .` passed. CI must
provide the PostgreSQL result before any artifact is considered releasable.

Known P2 supply-chain items remain open: the PostgreSQL `17.5-bookworm` image is version
tagged but not digest pinned, and the Rust Linux job installs apt packages from runner
repositories without content pins. Under the Phase 2 policy, these are P2 reproducibility
follow-ups and do not block the Phase 3 development gate. They are not authorization to
deploy or publish.
