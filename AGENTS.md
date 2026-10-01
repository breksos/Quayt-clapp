# Quayt team contract

The user and CLATCHPARATOR are the product managers. Only they assign work and approve scope.

## Ownership

- `QUAYT-BACKEND`: central service, database, migrations, domain rules, APIs, integrations, tenancy, audit, and durable events.
- `QUAYT-FRONTEND`: ClappKit desktop window, role workspaces, client state, accessibility, and GUI use of agreed APIs.
- `QUAYT-QA`: test strategy, security-negative tests, operational invariants, regression evidence, and acceptance gates.
- `QUAYT-RELEASE`: manifest, packaging, CI, artifacts, signing readiness, installers, updates, rollback, and release records.
- `QUAYT-SECURITY`: threat model, trust boundaries, authentication, authorization, tenant isolation, secrets, supply chain, and security gates.

Do not edit another owner's files without a PM-approved handoff. The shared working tree must never be reset or cleaned in a way that discards another agent's work.

## ClappKit is normative

`clappkit/` is a git submodule and must never be edited from this repository. Before implementation, read its `README.md` and the applicable documents under `clappkit/docs/`. Its package, protocol, architecture, format, and playbook rules are authoritative and must be followed exactly.

The clapp is one binary with two roles: a human window and an agent CLI. The authoritative local core is Rust. Both surfaces use the same state and logic. The CLI is mandatory and its help must match the manifest commands exactly. Signals carry notices, never durable state, and only human actions emit signals. Secrets never enter snapshots, CLI output, logs, or chat.

The original Python Quayt repository is read-only reference material. Reuse is decided explicitly from written inventories; do not copy files opportunistically. A retained Python service remains a separate central HTTPS service and source of operational truth. Do not bundle Python or PostgreSQL into the clapp without a PM-approved architecture decision.

## Current phase

Phase 3, the first vessel-call workspace, is authorized. Its scope and acceptance gates
are in `docs/phase-3.md`. Production deployment, publication, production access, legacy
data migration, mobile work, external contact, and operational mutations are excluded.
