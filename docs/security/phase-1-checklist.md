# Phase 1 security checklist

Security acceptance is fail-closed: a failed item blocks the affected build, startup,
route, or feature. Evidence belongs in automated tests or CI output. An explanation alone
does not satisfy a gate.

## Phase 1 release gate

- [ ] **Identity and tenancy:** tenant and actor context constructors reject missing,
  malformed, unknown, and unverified inputs. Tests prove there is no default tenant,
  anonymous privileged actor, header-based role grant, or implicit platform owner.
- [ ] **Production configuration:** production startup aborts when required settings are
  absent or unsafe, when a development auth bypass is enabled, when a mock provider is
  selected, or when incompatible values are combined. Development mode is explicit.
- [ ] **Transport:** production client configuration accepts only HTTPS service endpoints
  with normal certificate and hostname verification. Timeouts and response bounds are
  finite; failures produce a stable, non-sensitive error.
- [ ] **Credential containment:** credentials can enter only in the human window and are
  written only to private app data. Snapshot, CLI status/output, signals, logs, errors,
  crash context, and test fixtures cannot serialize token, password, cookie, or 2FA fields.
- [ ] **ClappKit boundary:** the submodule is unchanged and pinned; GUI and CLI use one
  Rust state; GUI/CLI IPC is distinct from the control pipe; no local TCP transport is
  introduced; Phase 1 declares and emits no signals.
- [ ] **Command authority:** `clatch validate .` passes, and the manifest command list is
  identical to CLI help and implemented dispatch. Phase 1 commands expose connection
  state only.
- [ ] **Service surface:** `/api/v1` contains no business mutation, public data, upload,
  webhook, integration, or admin route. Health responses disclose no configuration,
  dependency credentials, stack trace, tenant, or actor data.
- [ ] **Errors and logging:** structured logs use correlation identifiers and safe event
  fields. Automated checks cover authorization/configuration failures without logging
  request authorization headers, bodies containing secrets, or internal exceptions.
- [ ] **Supply chain:** Rust and Python builds use committed locks/pins from a clean
  environment; CI needs no private dependency credential and runs the documented local
  formatting, static, build, and test commands.
- [ ] **Acceptance:** QA records passing negative tests, Security records no unresolved
  Phase 1 P0/P1 issue, and every accepted P2 has an owner and due phase.

## Gates for later work

These gates apply before the named surface can be merged or enabled:

- [ ] **Login and sessions:** short-lived access credentials, rotating and revocable
  refresh credentials, single-use bootstrap/recovery flows, secure logout, bounded session
  lifetime, and explicit 2FA policy are specified and negatively tested. Missing, expired,
  replayed, revoked, or wrongly scoped credentials are denied.
- [ ] **Authorization and admin:** each route has an actor/tenant/capability policy enforced
  by the service. Platform-owner actions and tenant-admin actions use distinct capabilities
  and tests prove neither crosses the boundary.
- [ ] **Database and RLS:** every tenant-owned table and child has a non-null tenant key and
  forced RLS; the runtime role cannot bypass it. Cross-tenant read/write/delete, foreign-key,
  bulk, background-job, and transaction-reuse tests all fail closed.
- [ ] **Business mutations:** authorization, optimistic concurrency, idempotency, validation,
  and atomic append-only audit/outbox writes pass negative and rollback tests before the
  first mutation route is exposed.
- [ ] **Webhooks and providers:** raw-body signature verification, replay defense,
  idempotency, tenant mapping, secret rotation, bounded input, and production provider
  configuration pass tests before a callback URL or integration is enabled.
- [ ] **Uploads and remote fetches:** private tenant-scoped storage, file limits and type
  detection, safe parsing/retrieval, and SSRF controls including redirect revalidation pass
  adversarial tests before accepting files or URLs.
- [ ] **Web content:** untrusted content is text by default; HTML, URL, download, and
  WebView navigation paths have explicit allowlists. Any cookie-authenticated mutation has
  SameSite policy plus Origin and CSRF validation.
- [ ] **Signals and offline writes:** a PM-approved design proves signals contain no durable
  or secret state and originate only from human actions. Any offline mutation design has
  conflict, idempotency, authorization-recheck, and revocation behavior before use.
- [ ] **Packaging and publication:** release ownership approves provenance, dependency and
  license inventory, SBOM, checksums, platform signing/notarization, update verification,
  and rollback evidence before any artifact is published.
