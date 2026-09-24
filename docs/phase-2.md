# Phase 2 — Authentication and tenant sessions

## Outcome

Connect the Rust clapp to a separate Quayt development service, authenticate a human with
OIDC Authorization Code plus PKCE, and establish a tenant context that the service verifies
against its own membership records. Phase 2 ends with a connected/authenticated/tenant
status in the window and CLI. It exposes no port-operation data or mutation.

## Fixed architecture

1. The development service is separate from the original Quayt server and binds locally by
   default. PostgreSQL is the authoritative identity, tenant, membership, and session store.
2. Quayt stores no passwords. A native-app OIDC flow opens the system browser, uses PKCE,
   and returns through a bounded loopback callback.
3. The service validates issuer, audience, signature, algorithm, expiry, and subject for
   every access token. Missing configuration or verification fails closed in every mode.
4. A tenant identifier from the client is only a selection. The server constructs request
   context after confirming the verified subject has an active membership in that tenant.
5. Refresh/access credentials stay in the OS credential store. They never enter Rust shared
   snapshots, CLI arguments or output, Clatch signals, logs, errors, or repository files.
6. The GUI owns login, tenant selection, and logout. The Phase 1 agent CLI remains limited
   to non-secret status and window commands.
7. No runtime mock identity provider or authentication bypass exists. Tests may use local
   signing keys and deterministic fixtures that cannot be selected by production config.

## Workstreams

- **Backend:** PostgreSQL baseline and forward migration; OIDC verifier; identity, tenant,
  membership, and revocable device-session records; `/api/v1/auth/config`, `/api/v1/session`,
  tenant selection, and logout endpoints; explicit request context dependency.
- **Frontend:** service URL setup, health/config discovery, system-browser PKCE login,
  loopback callback, OS credential storage, refresh/logout, tenant picker, and shared
  non-secret connection/session snapshot.
- **Security:** auth/session threat model, callback and token-containment review, tenant
  authorization matrix, and negative-test gate.
- **QA:** PostgreSQL-backed migration and membership tests, invalid-token matrix, client
  snapshot/CLI secret checks, reconnect/refresh/logout cases, and cross-tenant denial.
- **Release:** development-server configuration and CI services only. Production deployment,
  packaging, signing, and publication remain excluded.

## Required contract

- `GET /health/live` and dependency-aware `GET /health/ready`.
- `GET /api/v1/auth/config` returns public OIDC/native-client metadata only.
- `POST /api/v1/session` verifies an OIDC access token and creates a device session.
- `GET /api/v1/session` returns verified actor plus active tenant memberships.
- `POST /api/v1/session/tenant` accepts a tenant UUID and succeeds only for an active
  membership; responses contain no bearer or refresh credential.
- `POST /api/v1/session/refresh` verifies a refreshed OIDC access token and atomically
  rotates the Quayt session credential.
- `POST /api/v1/session/logout` revokes the current device session and is idempotent.
- Authenticated errors use the existing safe envelope and do not reveal whether another
  tenant, user, or membership exists.

The desktop completes the provider's native-app code exchange and keeps provider access and
refresh tokens only in the OS credential store. The service accepts a provider access token
only when creating or refreshing a device session, verifies it completely, and stores only
its digest. It returns a one-time opaque Quayt session credential; PostgreSQL stores only a
keyed hash. Normal Quayt requests use that revocable credential. Refresh rotation is atomic,
replay revokes the credential family, and logout revokes the complete device session. `GET`
routes are read-only and never create or repair session state.

## Acceptance gates

- Clean PostgreSQL migration and schema-drift checks pass.
- Missing, malformed, expired, wrongly signed, wrong-issuer, wrong-audience, and disallowed-
  algorithm tokens are denied.
- Tenant selection, direct access, and reused-request-context cross-tenant tests fail closed.
- Login state, tokens, verifier material, and callback codes cannot appear in snapshots,
  CLI output, signals, logs, or persisted non-secret state.
- PKCE uses S256, state is single-use and unpredictable, callback binding is loopback-only,
  and timeout/cancellation/replay behavior is tested.
- Logout and refresh rotation revoke the prior credential path.
- Rust, web, service, QA, manifest, and CI checks pass; Security reports no unresolved P0/P1.

## Excluded

Port dashboards, vessel calls, containers, public tracking, finance, integrations, offline
writes, mobile workflows, legacy data migration, production infrastructure, and release
publication remain later phases.
