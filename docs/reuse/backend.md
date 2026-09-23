# Backend reuse assessment

## Scope and evidence

This assessment covers the original Quayt repository at commit
`6202459d4f14e73109ff40d1fb81c0ade6f2760a`. The source was inspected read-only.
No implementation was copied.

The original backend is a Python 3.11/FastAPI/SQLAlchemy/PostgreSQL monolith with
94 mapped tables, 99 Alembic revisions, 84 route modules, 746 route decorators,
266 route-local Pydantic schemas, and 1,215 statically identified test functions.
The migration graph has one head and no missing parent revision at this commit.
Those counts describe breadth, not readiness: important guarantees are implemented
in middleware and route code, and several security and concurrency paths fail open.

ClappKit fixes the client boundary: one Rust binary serves the human window and the
agent CLI; both use the same local core. Signals are notices and never durable state.
The central HTTPS service and PostgreSQL must remain the authority for operational,
financial, identity, audit, and integration state.

## Recommendation

Retain Python for the central service, but do not expose the existing monolith to the
clapp unchanged. Build a versioned `/api/v1` contract around audited domain commands,
queries, durable change cursors, and explicit tenant identity. Start from a reviewed
PostgreSQL baseline rather than replaying the existing migration chain as the new
architecture's foundation.

Only the deterministic, side-effect-free Python utilities below are candidates for
direct reuse. Models and tests contain valuable domain knowledge, but most domain
transitions live in FastAPI routes and are coupled to SQLAlchemy sessions, implicit
middleware tenancy, HTML concerns, and ad hoc commits. Extract that behavior into
central application services before a client contract is frozen.

## Reuse classification

| Classification | Concrete source areas | Decision and conditions |
|---|---|---|
| **Reuse directly** | `app/services/baplie_parser.py` | Pure dataclass-based EDIFACT parsing with no database dependency. Retain in the central Python service. Add golden BAPLIE/COPRAR fixtures, malformed-input limits, and standards conformance cases before treating it as an integration boundary. |
| **Reuse directly** | Pure portions of `app/services/geofence.py`, `app/services/contract_volume_pricing.py`, `app/services/laytime.py`, and `app/services/bdn_validator.py` | Reuse only isolated deterministic functions such as distance math, tier selection, laytime arithmetic, and Reg 18 field checks. Split configuration, filesystem, model imports, and database lookup from the pure calculations first. Existing focused tests include `tests/test_b15_volume_pricing.py`, `tests/test_f5c_laytime.py`, and `tests/test_bdn.py`. |
| **Reuse directly** | `app/utils/dt.py`, `app/utils/pagination.py`, `app/utils/retry.py` | Small central-service utilities may be retained after confirming UTC, pagination, and retry semantics in the new API contract. They do not belong in the Rust client unless the client needs an equivalent presentation helper. |
| **Adapt behind a new contract** | `app/models/*.py` | Preserve the domain inventory and relationships, including vessel calls, stowage, yard, gate, work orders, CFS, warehouse, calculations, invoices, payments, contracts, and master data. Redesign constraints, tenant ownership, money types, lifecycle enums, and audit metadata before establishing the new schema. Do not reuse ORM models as wire DTOs. |
| **Adapt behind a new contract** | `alembic/versions/*.py`, `alembic/env.py` | Use the 99 revisions as schema history and data-migration evidence. Create a reviewed baseline for the isolated service, then add forward-only migrations. The current RLS revision covers only its original 24-table list and was never extended for many later tenant-bearing tables. |
| **Adapt behind a new contract** | `app/routes/vessel_calls.py`, `work_orders.py`, `operations.py`, `stowage*.py`, `yard.py`, `gate.py`, `arrival.py`, `warehouse*.py`, `release_control.py` | These contain the operational workflows and state-transition guards. Move transitions into transaction-scoped application services with explicit commands, legal transition tables, actor checks, optimistic versions, idempotency keys, and event creation in the same transaction. Routes become thin `/api/v1` adapters. |
| **Adapt behind a new contract** | `app/routes/calculations.py`, `tahakkuk.py`, `invoices.py`, `cashier.py`, `acente_billing.py`; `app/services/hizmet_hesabi.py`, `cfs_calculation.py`, `warehouse_calculation.py`, `aging_service.py`, `release_gate.py` | Retain business rules centrally, but replace binary floating-point money with `Decimal`/PostgreSQL `NUMERIC`, define rounding and FX snapshot rules, and make finalize/refund/revoke/release commands atomic and idempotent. Financial totals must never be recomputed authoritatively in the clapp. |
| **Adapt behind a new contract** | `app/auth.py`, `app/routes/auth.py`, `auth_2fa.py`, `admin_users.py`, `saas.py`, `saas_admin.py`, `tenant_setup.py` | Preserve bcrypt, TOTP, lockout, token-version revocation, tenant slug login, and role concepts as behavioral inputs. Replace cookie-only browser assumptions with a documented clapp session flow, short-lived access tokens, rotating/revocable refresh credentials, explicit platform-owner scopes, and fail-closed authentication. |
| **Adapt behind a new contract** | `app/models/event_log.py`, `outbox.py`, `processed_callback.py`; `app/events.py`, `services/event_replay.py`, `outbox_dispatcher.py`, `webhook_security.py` | Preserve after-commit notification, replay, callback idempotency, retry, and DLQ intent. Replace them with an append-only audit envelope plus a transactional outbox claimed with row locking. Expose tenant-scoped monotonic cursors for synchronization. |
| **Adapt behind a new contract** | `app/services/baplie_generator.py`, `manifest_parser.py`, `ax_generator.py`, `efatura/`, `payment/`, `notification/`, `scale/`, `ais_*`, `fx_provider.py`; related routes | Keep provider interfaces, formats, and domain mappings centrally. Replace mocks and fallback-to-mock behavior with explicit environment configuration, signed callbacks, tenant-scoped credentials, egress allowlists, request idempotency, and provider contract tests. |
| **Adapt behind a new contract** | `app/routes/reports.py`, `operational_reports.py`, `sector_reports.py`; `app/services/operational_kpi.py`, `report_builder.py`, `voyage_comparison.py`; PDF/Excel generators | Keep report definitions and formulas on the server. Separate query specifications from FastAPI/Jinja output, version export schemas, enforce tenant predicates, and generate official financial/operational artifacts centrally. |
| **Adapt behind a new contract** | `app/services/plan_tier.py`, `app/models/tenant.py`, `app/routes/saas*.py`, `app/routes/usage.py` | Feature names and plan concepts are reusable. Current code is configuration and manual plan mutation, not billing: it lacks subscription/customer/payment-provider state, metering integrity, invoice lifecycle, entitlement snapshots, and renewal/grace-period handling. Build billing as an audited server domain. |
| **Behavioral reference only** | All `app/routes/*.py` request/response shapes and inline Pydantic models | The route surface is unversioned and mixes HTML, JSON, serialization, transactions, and domain decisions. Derive use cases from it, but publish new stable DTOs, errors, pagination, concurrency tokens, and deprecation rules. |
| **Behavioral reference only** | `tests/test_*.py` integration and workflow tests | Port domain cases and expected transitions. Do not adopt the harness unchanged: it disables `AUTH_HARDENING`, uses `create_all`, and does not exercise migrated PostgreSQL RLS as deployed. Add migration-from-baseline, RLS-negative, authorization, concurrency, idempotency, and reconnect tests. |
| **Behavioral reference only** | `app/scheduler.py`, `services/*_cron.py`, `daily_digest.py`, `planning_morning_cron.py`, `pre_arrival_notifier.py` | Retain schedules and missed-run business rules. Run jobs in a separately owned central worker with database leases/advisory locks and durable run records. APScheduler's SQLAlchemy job store is persistence, not sufficient distributed execution locking. |
| **Discard** | `app/ai/copilot.py`, `app/ai/ocr.py`, LLM portions of `app/ai/*`, `app/routes/ai_router.py` | Do not place probabilistic AI in authoritative operational paths. Any future advisory feature must be optional, separately authorized, evidence-bearing, and unable to mutate domain state without a deterministic command. Rule-based calculations may be retained under the deterministic service boundary. |
| **Discard** | `app/cli.py` as the product CLI; Jinja/template/static/PWA behavior; `demo_seeder.py`; deployment/update/admin-shell routes | The agent CLI must be the Rust role of the ClappKit binary. UI and release-owned assets are outside this backend report. Demo, deployment, system-update, backup-shell, and production-control endpoints do not belong in the application API. |

Direct reuse remains subject to confirming repository provenance and the declared MIT
license before code is transferred into the new codebase.

## Central Python service boundary

The central service must own:

- PostgreSQL schema, migrations, tenant membership, users, roles, sessions, and
  authorization decisions;
- all operational aggregates and transitions: vessel call, berth, stowage, container
  movement, yard/gate, CFS, warehouse, work order, weighing, and release control;
- tariffs, contracts, calculations, FX snapshots, invoices, payments, refunds,
  receivables, entitlements, subscriptions, and billing;
- append-only audit records, transactional outbox, webhook receipts, idempotency
  records, durable integration attempts, scheduler leases, and job-run history;
- BAPLIE/EDI, AIS, scale, e-fatura, payment, email/SMS/push, ERP/customs/VTS adapters;
- authoritative reports, PDFs, spreadsheets, compliance exports, and retention rules;
- `/api/v1` command/query contracts and a tenant-scoped synchronization feed.

The service should expose commands with an idempotency key and expected aggregate
version. A successful mutation should commit the aggregate change, audit record, and
outbox entry atomically. Read models and reports should return an opaque server cursor or
revision so the client can recover after disconnection without interpreting Clatch
signals as state.

## Rust clapp boundary

The Rust core must own both local surfaces and all device-local responsibilities:

- ClappKit role dispatch, GUI-to-CLI IPC, command dispatch, manifest-aligned help, and
  snapshots shared by the window and CLI;
- the HTTPS client, retry classification, request idempotency keys, server revision
  cursors, reconnect/backoff, and deterministic mapping of server errors;
- a bounded local cache of non-secret server projections, connection status, pending
  presentation state, and explicit stale/offline markers;
- login initiated by the human window and local protection of refresh credentials.
  Credentials must never appear in CLI arguments/output, snapshots, logs, signals, or
  chat; the CLI may expose only connection/session status;
- local validation needed for immediate feedback, while accepting the server's decision
  as authoritative on every command;
- Clatch signals emitted only for human actions. A signal contains a notice and stable
  identifier, never an operational payload or synchronization state.

The Rust cache is not an offline database and must not allocate official identifiers,
finalize financial records, decide legal transitions, or merge concurrent operational
writes. If offline mutation is later required, it needs a separate PM-approved protocol
with server-issued operation IDs, explicit conflict states, and auditable reconciliation.

## Required API and synchronization contract

Before implementation, backend and frontend owners should agree on:

1. `/api/v1` resource and command names, stable DTOs, ISO-8601 UTC timestamps, decimal
   money strings, currency codes, pagination, and a versioned error envelope.
2. Authentication bootstrap and refresh, tenant selection, role/scope claims, device
   session revocation, 2FA, and secret storage rules for the GUI/CLI pair.
3. Optimistic concurrency using an aggregate `version`/ETag and `If-Match` on every
   contested mutation, with `409`/`412` conflict responses carrying current state.
4. Mutation idempotency using a client-generated operation UUID scoped to tenant,
   actor, and command; retries return the original result.
5. A durable tenant change feed ordered by server sequence, with cursor paging,
   retention, snapshot resync, deletion/tombstone semantics, and authorization filtering.
6. Explicit capability endpoints for plan entitlements and server-supported API/schema
   versions. The client must not infer privileges from hidden UI controls.

## Major blockers

These issues must be resolved before the original backend can serve the clapp:

- **Incomplete tenant isolation.** `e5f6a7b8c9d0_postgresql_rls_tenant_isolation.py`
  applies RLS to a fixed early list of 24 tables. Many later tenant-bearing tables have
  no RLS policy. Child tables without their own `tenant_id` can also be queried directly.
  The ORM hook filters only SELECT statements, permits `skip_tenant_filter`, and treats a
  missing context as global access with `app.bypass_rls='on'`.
- **Fail-open authentication.** `app/main.py` continues when the JWT's user is absent
  from the database, trusting token role and tenant until expiry. Tests disable
  `AUTH_HARDENING`, so this path is not covered under production settings.
- **Unsafe initial-password flow.** `app/routes/auth.py` skips password verification for
  any user with `parola_degistir_gerekli=True`; knowledge of tenant slug and username is
  enough to obtain a session and set a password. Replace this with a short-lived,
  single-use, hashed bootstrap credential delivered out of band.
- **Cross-tenant administration.** `app/routes/saas_admin.py` uses `require_admin` for
  arbitrary `tenant_id` plan upgrades, white-label updates, and onboarding instead of
  the platform-owner guard used elsewhere. A tenant admin can target another tenant.
- **Webhook environment mismatch.** `services/webhook_security.py` requires secrets only
  when `APP_ENV=production`, while deployment sets `ENVIRONMENT=production`. Missing
  e-fatura/payment webhook secrets therefore enable the development bypass in production.
- **Public mock scale ingestion.** `routes/scale_webhook.py` accepts a caller-selected
  `provider` query parameter; `scale/mock.py` always approves signatures. A public caller
  can select `mock` and create weighing records. Provider selection must be server-side
  and production must fail closed.
- **Outbox is not yet durable delivery.** `outbox_dispatcher.enqueue()` commits internally,
  so it is not atomic with the initiating domain transaction. `dispatch_pending()` does
  not claim rows with `FOR UPDATE SKIP LOCKED`; concurrent workers can select the same
  message. It is not registered as a scheduler job at this commit, leaving delivery
  dependent on the manual admin endpoint or an undocumented external runner.
- **Scheduler coordination is overstated.** The code assumes a PostgreSQL APScheduler
  job store prevents multiple app workers from executing the same job. It persists jobs
  but does not provide the required single-owner execution guarantee. Several jobs also
  run without explicit tenant context and rely on global access.
- **Audit is mutable application data.** `EventLog` has no database append-only guard,
  actor ID, request/correlation ID, schema version, or immutable canonical payload.
  Human-language messages and in-memory/Redis SSE are useful notifications but not a
  durable audit or synchronization protocol.
- **Money and state rules are route-coupled.** Financial services and routes frequently
  convert values to `float`; transitions are string comparisons spread through large
  route modules. This prevents a stable contract and risks inconsistent rounding,
  authorization, and partial commits.
- **API coupling is too broad.** The 746 route decorators are unversioned, schemas are
  mostly declared inside route files, and many handlers serialize ORM objects manually.
  Exposing this surface would lock the Rust clapp to the monolith's internal shape.
- **Billing is only a feature matrix.** `services/plan_tier.py` and tenant fields provide
  manual plan gates but no authoritative subscription ledger, metering, provider
  reconciliation, renewal, grace period, or billing audit.
- **Integration readiness varies.** Several ERP/customs/VTS and payment/e-fatura paths
  are mocks or fallback to mocks. Generic webhook delivery accepts stored URLs and
  headers without a documented egress policy. Production adapters require explicit
  configuration validation, secret ownership, allowlists, and contract tests.

## Test reuse and acceptance gates

Keep the existing tests as a behavioral catalogue. Directly retain focused unit tests
only with the pure modules they exercise. Rebuild HTTP and database tests around the new
versioned contract and real migrations.

Minimum backend gates are: clean baseline migration plus upgrade tests; RLS enabled and
forced for every tenant-owned table; cross-tenant read/write/delete/foreign-key negative
tests; fail-closed auth under production settings; platform-owner authorization tests;
state-machine transition tables and concurrency conflicts; exact decimal/rounding cases;
atomic audit/outbox assertions; duplicate command and callback races; multi-worker job
and outbox claiming; cursor replay, truncation, tombstones, and full resync; and provider
configuration that cannot silently select a mock in production.

## Handoffs

- **Shared contract:** backend and frontend owners must jointly freeze auth, `/api/v1`,
  optimistic concurrency, idempotency, and synchronization semantics before either
  surface implements commands.
- **Security:** review the tenant/RLS matrix, bootstrap authentication, platform-owner
  scopes, webhook fail-closed behavior, credential storage, and egress policy.
- **QA:** turn the blockers above into PostgreSQL-backed negative and concurrency gates;
  do not rely on the current fixture mode that disables production auth hardening.
- **Release:** the eventual central service and worker need distinct health/readiness,
  migration, rollback, and configuration validation gates. No deployment conclusion is
  implied by this reuse assessment.
