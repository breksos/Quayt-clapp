# Phase 3 security gate

Date: 2026-09-25

**Verdict: PASS — no unresolved P0/P1.**

Final Phase 3 acceptance remains conditional on the configured PostgreSQL CI job passing
the migration, drift, RLS, pool-reuse, and repository tests. This is an execution-evidence
gate, not a security finding.

## P0/P1 blockers

None.

### Resolved P1 — Database tenant isolation

The migration enables and forces RLS, creates a fail-closed tenant policy, and creates a
non-login, non-owner, non-superuser, non-`BYPASSRLS` runtime role
(`service/migrations/versions/0002_vessel_calls.py:21-66`). Every vessel-call transaction
uses `SET LOCAL ROLE`, verifies the live role/table security attributes, and sets the
tenant through transaction-local `set_config` before querying
(`service/src/quayt_service/vessel_calls.py:17-60,142-215`). Explicit tenant predicates
remain on list, detail, summary, and filtered queries.

PostgreSQL tests cover missing, empty, malformed, and foreign tenant context; cross-tenant
reads and writes; row movement; commit and rollback pool reuse; role and policy tampering;
`row_security=off`; role attributes; repository and seed execution under the restricted
role; filters; detail; summary; and deterministic seed behavior
(`service/tests/test_postgres_vessel_calls.py:42-326`). Unit tests also prove repository
operations and readiness fail closed if the role/RLS assertion is false
(`service/tests/test_vessel_call_security.py:14-43`).

### Resolved P1 — Desktop workspace binding

Tenant selection, logout, and service reconfiguration synchronously invalidate request
generations and clear operational state before awaiting locks, persistence, or network
work. List, summary, detail, success, and error responses commit only when service URL,
session generation/version, selected tenant, and request generation still match
(`src-tauri/src/state.rs:286-319,526-733,889-963`).

Deterministic delayed-response tests cover every success/error combination across tenant
change, logout, service reconfiguration, session replacement, and a newer request. They
also verify transition-time clearing and failed reconfiguration
(`src-tauri/src/state/vessel_response_tests.rs:70-321`).

## P2 follow-ups

1. The Phase 3 runtime role receives `INSERT` and `UPDATE` so the development seed can use
   the same RLS path (`service/migrations/versions/0002_vessel_calls.py:64-66`). Before any
   production architecture or broader mutation scope, split the read-only service role
   from an explicit development seed role and prove the service role cannot write.
2. Accepted Phase 2 follow-ups remain: complete native ID-token cryptographic validation,
   add absolute/idle session expiry, enrich non-secret auth audit details, and strengthen
   dependency/CI pinning (`docs/security/phase-2-review.md`).

## Other controls verified

- Vessel routes use the real `require_request_context`; tests cover missing, invalid,
  revoked, membership-removed, and switched-tenant sessions. Foreign details return a
  generic 404 (`service/src/quayt_service/vessel_call_routes.py:54-80`;
  `service/tests/test_vessel_calls.py:104-176`).
- Query values are typed, parameter-bound, wildcard-escaped, and bounded by status, search
  length, UTC range, limit, and offset (`service/src/quayt_service/vessel_calls.py:63-103,
  154-185`).
- Operational responses, including errors, receive `Cache-Control: no-store` and
  `Pragma: no-cache` (`service/src/quayt_service/app.py:100-109`;
  `service/tests/test_vessel_calls.py:37-67,121-141`).
- The seed refuses non-development mode before database access and contains only
  deterministic fictional data (`service/src/quayt_service/seed_vessel_calls.py:18-65`).
- Operational data remains in the GUI projection only; the five-command CLI, Clatch signal
  surface, settings, and credential handling remain free of operational data and secrets.
  Rust sentinel tests cover both secret and vessel-call containment.
- The migration is forward-only, collision-safe for its cluster role, uses tenant-leading
  indexes and constraints, and performs narrow downgrade revocation without destructive
  `DROP OWNED` or `CASCADE` behavior.

## Evidence conditions

- PASS locally: Phase 1, Phase 2, and Phase 3 dependency-free acceptance checkers.
- PASS locally: `npm run build:web`.
- PASS locally: `cargo test --manifest-path src-tauri/Cargo.toml --locked` (29 tests).
- PASS locally: manifest validation and `clatch validate .`.
- PASS locally: clean ClappKit gitlink and `git diff --check`.
- NOT RUN LOCALLY: Python/service and PostgreSQL tests; the available Python lacks pytest
  and this host has no PostgreSQL/container runtime. `docs/qa/phase-3-results.md` records
  39 service tests passing with six PostgreSQL tests skipped, but its Rust failure notes
  predate the current passing 29-test run and must be refreshed by QA.
- Final acceptance requires `.github/workflows/build.yml` to pass `alembic upgrade head`,
  `alembic check`, and the complete service test suite against PostgreSQL, with no RLS or
  vessel-call integration skips.
