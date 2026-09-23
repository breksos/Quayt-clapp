# Phase 1 — Secure foundation

## Outcome

Create a runnable, tested foundation for Quayt without importing legacy business code.
The Rust ClappKit client and the Python central service remain separate processes with an
explicit HTTPS/JSON boundary. PostgreSQL and the service will become authoritative for
operational data; Phase 1 does not implement operational data.

## Workstreams

- **Rust clapp (`QUAYT-FRONTEND`)**: one binary with window and CLI roles; shared local
  state; manifest-aligned `status`, `show`, `hide`, `quit`, and `ping` commands; connection
  setup/status only; no secret in snapshots, CLI output, logs, or signals.
- **Python service (`QUAYT-BACKEND`)**: versioned service skeleton, configuration
  validation, liveness/readiness, explicit tenant and actor context types, and a stable
  error envelope. No implicit/default tenant, development auth bypass, mock-provider
  fallback, or business mutation route.
- **Security (`QUAYT-SECURITY`)**: threat model and security acceptance review covering
  identity, tenant isolation, credentials, service/client trust, webhooks, audit, and
  supply chain.
- **QA (`QUAYT-QA`)**: executable Phase 1 acceptance checks for the manifest/CLI contract,
  fail-closed service configuration, tenant-context invariants, and both build surfaces.
- **Release (`QUAYT-RELEASE`)**: build-only CI and reproducibility checks after the two
  implementation workstreams land. Packaging and publication remain out of scope.

## Fixed decisions

1. ClappKit is normative and its submodule is immutable.
2. The local core and both clapp surfaces are Rust. The central service is Python.
3. The service API begins at `/api/v1`; health endpoints are outside the versioned domain
   surface.
4. Server-issued identity and tenant membership are authoritative. A tenant identifier
   supplied by a client never grants access by itself.
5. Credentials are entered by a human in the window and stored in the private app data
   directory. They never enter CLI arguments/output, shared snapshots, signals, or logs.
6. Phase 1 has no offline mutation queue and emits no Clatch signals.
7. Legacy Quayt remains read-only reference material; transfer requires an explicit later
   work order and provenance check.

## Acceptance gates

- `clatch validate .` succeeds and manifest commands exactly match CLI help.
- Rust formatting, compilation, and tests pass using the pinned ClappKit submodule.
- Python formatting/static checks and tests pass from a clean environment.
- Service startup refuses unsafe or incomplete production configuration.
- Tenant/actor context construction has negative tests proving there is no fallback
  tenant or anonymous privileged context.
- The clapp's snapshot and CLI status contain connection state only and cannot serialize
  credentials.
- Security review records no unresolved Phase 1 P0/P1 issue.
- CI runs the same build and test commands locally documented for contributors.

## Excluded

Login protocol implementation, PostgreSQL schema, RLS policies, port workflows, container
tracking, integrations, billing, mobile application, offline writes, packaging, signing,
deployment, and release publication belong to later PM-approved phases.
