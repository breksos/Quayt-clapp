# Phase 2 security gate

**Verdict: PASS — no unresolved P0/P1.**

Final Phase 2 acceptance remains conditional on the configured PostgreSQL CI job passing
the migration, schema-drift, and repository tests. This is an evidence gate, not a security
finding.

## P0/P1 blockers

None.

The prior P1 is resolved. Every provider-token digest is unique across credential
generations (`service/src/quayt_service/models.py:114-120`; migration
`service/migrations/versions/0001_auth_sessions.py:105-117`). Creation and refresh take a
transaction-scoped advisory lock before checking the digest
(`service/src/quayt_service/repository.py:83-85,176-196,318-328`), with the database unique
constraint as the final backstop. Same-token retries are accepted only for the same active
subject, client instance, session, and current generation. The route deterministically
recovers that session's credential (`service/src/quayt_service/auth_routes.py:74-93`;
`service/src/quayt_service/security.py:16-23`); other reuse is denied. Actor row locking
serializes the active-session cap (`service/src/quayt_service/repository.py:217-243`), and
refresh reuse is rejected before credential rotation.

Fake-repository tests cover idempotent retry, cross-client token reuse, concurrent creation,
refresh-token reuse, and the cap (`service/tests/test_app.py:163-243`). PostgreSQL tests
exercise binding/retry/reuse/cap and concurrent token and cap races
(`service/tests/test_postgres_repository.py:312-493`).

## P2 follow-ups

1. `src-tauri/src/auth.rs:796-831` decodes ID-token claims without validating its signature,
   expiry, or `azp`. The service independently verifies the access token, limiting current
   authority impact, but the native OIDC response-binding check is incomplete. Verify the
   ID token cryptographically against provider keys and add negative tests.
2. `service/src/quayt_service/repository.py:381-383` resets the full 30-day expiry at every
   refresh. Add a separate immutable absolute expiry and a shorter idle expiry.
3. `service/src/quayt_service/repository.py:82-99` writes empty audit details, so tenant
   selection and session replacement do not identify the affected tenant or replaced
   lineage. Add non-secret identifiers while preserving the append-only trigger.
4. CI dependencies remain reproducibility follow-ups: `service/requirements.lock` has exact
   versions without hashes, the PostgreSQL image is tag-pinned rather than digest-pinned,
   and `.github/workflows/build.yml:56` uses a mutable action tag.

## Evidence run

- PASS: `python3 qa/phase2_acceptance.py`
- PASS: `cargo test --manifest-path src-tauri/Cargo.toml --locked` (16 tests)
- PASS: `npm run build:web`
- PASS: `npm run validate:manifest` and `clatch validate .`
- PASS: ClappKit gitlink clean; manifest declares no signals and no auth CLI commands.
- REVIEWED: focused fake-repository coverage includes the repaired token-binding and
  session-cap behavior; execution is configured in the service CI job below.
- NOT REPRODUCED LOCALLY: PostgreSQL integration because this host has no recorded
  PostgreSQL run. `docs/qa/phase-2-results.md` marks PostgreSQL CI evidence pending. Final
  acceptance requires `.github/workflows/build.yml:66-103` to pass, including migration,
  drift, and PostgreSQL repository tests.

## Reviewed controls that are effective

Access-token verification pins RS256 and checks signature, issuer, audience, expiry, issue
time, and subject. Quayt credentials use keyed hashes; rotation is row-locked and replay
revokes the family. Logout is idempotent. Request context reloads active membership on
every request. The callback is loopback-only, bounded, cancellable, timed, and single-use.
Secrets are isolated from serializable snapshots and the CLI; GUI auth commands and Clatch
signals are absent. Configuration has no runtime auth-bypass selector, CORS has explicit
origins, and auth audit update/delete is blocked by a database trigger.
