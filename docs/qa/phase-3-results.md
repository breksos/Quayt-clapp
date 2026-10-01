# Phase 3 QA results

Status: **PASS locally; PostgreSQL and Security closure pending**

The Phase 3 acceptance checker covers tenant predicates for list, detail, and summary;
bounded and malformed filters; deterministic development-only seed behavior; migration
ownership and indexes; accessible GUI workspace states; and continued exclusion of
vessel-call data from the five-command agent CLI.

Latest local evidence:

- Phase 1, Phase 2, and Phase 3 acceptance checkers passed.
- Service tests passed: 50 passed, 22 skipped. The skipped PostgreSQL repository and vessel-call
  integration tests require `QUAYT_DATABASE_URL`; no local PostgreSQL server or container
  runtime was available.
- Web build, manifest validation, `clatch validate .`, Ruff format/lint, mypy, and
  `git diff --check` passed.
- Rust formatting and locked tests passed: 29 tests. Delayed list, summary, and detail tests use
  the production `receive_vessel_calls` and `receive_vessel_detail` commit paths; stale
  responses cannot commit after tenant switch, logout, reconfiguration, session change, or a
  newer request.
- Phase 3 now has static QA evidence for forced RLS, transaction-local tenant context, restricted
  runtime role, pooled-connection reuse, and runtime-role bypass denial. Execution of those
  PostgreSQL assertions remains pending CI/live PostgreSQL.

## PM local demo handoff

The deterministic vessel-call seed is development-only and remains in the service, never the
clapp. A PM can run PostgreSQL with `docker compose -f compose.dev.yml up -d postgres`, apply
migrations, and execute `quayt-seed-vessel-calls --tenant-id <active-demo-tenant>`. This
workstation cannot exercise that path because it has no Docker/PostgreSQL runtime.

The repository deliberately supplies no development OIDC provider, active demo tenant, or demo
membership, so the desktop cannot yet be exercised end-to-end from a URL alone without violating
the no-auth-bypass rule. Smallest handoff: **Backend** should provide a development-only,
non-bypass provisioning command for a deterministic active tenant/membership; **Release** should
document a local loopback OIDC provider configuration. QA can then run and record the seeded PM
workspace flow.
