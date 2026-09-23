# Phase 1 threat model

## Scope

Phase 1 establishes a Rust ClappKit client and a separate Python service skeleton. It
contains connection status and service context types, but no login protocol, operational
data, database schema, mutation route, integration, upload, or deployment. This model
sets the boundary those later features must preserve; it does not repeat the legacy audit.

## Trust boundaries and protected assets

| Boundary | Trust rule | Protected assets |
|---|---|---|
| Human window -> Rust core | Credentials may enter only through the window. The shared state receives safe status, never secret material. | Access/refresh credentials, 2FA material, local configuration |
| CLI -> local Rust core | The CLI is an agent-controlled, separately grantable surface over the app's private IPC. CLI input is untrusted. | Shared state, command authority, private app data |
| Clatch control pipe -> clapp | This channel is separate from app IPC. The injected one-time token proves the spawned instance within the same-OS-user boundary. | Instance identity, agent binding metadata |
| Rust client -> central service | The network is hostile. HTTPS authenticates the service; only verified service responses establish actor, tenant membership, capabilities, and authoritative state. | Session, actor and tenant context, future operational data |
| Request -> Python service | Headers, path/query values, bodies, and client tenant identifiers are untrusted. Missing or invalid identity has no authority. | Tenant isolation, authorization, admin boundary |
| Service -> PostgreSQL/providers | Database and provider credentials stay server-side. Application checks do not replace future RLS or provider verification. | Tenant data, audit trail, webhook secrets, provider credentials |
| Source -> build -> package | A lockfile hash proves integrity only to its recorded source; ClappKit packages currently carry no authorship signature. | Source provenance, dependency integrity, release artifacts |

The local boundary assumes one OS user. It does not protect secrets from a process already
running as that user. Remote service compromise and host compromise remain outside the
protection Phase 1 can provide.

## Main threats and mandatory controls

| Threat | Required control |
|---|---|
| Credential disclosure through state, CLI, signals, logs, errors, or chat | Secret-bearing types stay outside shared snapshots and command responses by construction. Log only safe metadata. Store credentials only in the ClappKit private app-data path with restrictive permissions and atomic writes. |
| Client-forged actor, tenant, role, or admin status | Construct actor and tenant contexts only from verified server identity. Treat every client-supplied tenant value as a selector that still requires membership. Never infer a tenant, privileged actor, or platform owner. |
| Fail-open production setup | Typed production configuration must require security-critical values, forbid development bypasses and mock fallbacks, and abort startup on missing, conflicting, or unsafe values. |
| Network interception or endpoint substitution | Production service URLs require HTTPS with certificate and hostname validation. No insecure verification switch may work in production. Apply bounded connect/request timeouts and response-size limits. |
| Local channel confusion or command escalation | Use ClappKit IPC and control-pipe primitives unchanged. Keep the two channels separate, use no local TCP listener, bound frames, and close on malformed framing. Manifest commands and CLI help must match exactly. Phase 1 declares and emits no signals. |
| Injection, XSS, CSRF, and unsafe navigation | Parse requests into bounded typed values; never construct queries, commands, templates, or paths by concatenation. Render service content as text. Allow only explicit navigation schemes/hosts. A future cookie-authenticated mutation also requires Origin and CSRF validation. |
| SSRF through future integrations, imports, or callbacks | Do not fetch user-supplied URLs. Any later fetch feature needs an allowlist, DNS/IP checks including redirects, blocked private/link-local destinations, and response/time limits. |
| Cross-tenant database access | Before tenant data exists, require non-null tenant keys, forced RLS for every tenant-owned table and child, a non-bypass application role, and negative cross-tenant CRUD and foreign-key tests. |
| Forged or replayed webhooks | Before enabling a webhook, require a configured secret, signature verification over the raw body, timestamp/replay limits, idempotency, explicit tenant mapping, and no production mock fallback. |
| Malicious uploads | Before accepting a file, require size/count limits, server-side type detection, safe generated names, private/quarantined storage, parser isolation, and tenant-authorized retrieval. Never execute or serve uploads as active content. |
| Audit suppression or mutation | Before business mutations, write an append-only audit event and durable outbox record in the same transaction, with verified actor/tenant, request and idempotency identifiers. The application role must not update or delete audit rows. |
| Dependency or artifact substitution | Pin the ClappKit submodule and dependency locks, build with locked resolution, require no private dependency credentials, and run the documented checks in clean CI. Publication remains blocked until release provenance and authenticity controls are approved. |

## Security invariants

- Denial is the default for absent identity, tenant membership, capability, route policy,
  production configuration, provider verification, or security metadata.
- The service authorizes every request. UI visibility and CLI command grants do not grant
  service authority.
- Platform administration is explicit and separate from tenant administration; neither
  role implies the other.
- Durable state travels through the service or shared core. Signals are notices only and
  only human actions may emit them; Phase 1 emits none.
- Errors expose stable codes and correlation identifiers, not secrets, stack traces,
  database details, or authorization reasoning that reveals another tenant's existence.
