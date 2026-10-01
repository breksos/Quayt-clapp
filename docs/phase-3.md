# Phase 3 — Vessel-call workspace

## Outcome

Deliver the first useful Quayt workflow: an authenticated, tenant-isolated, read-only
vessel-call dashboard backed by the local development service and seeded development data.
No rented or production server is required.

## Backend scope

- Add a forward PostgreSQL migration and model for tenant-owned vessel calls.
- Expose authenticated list, detail, and summary endpoints under `/api/v1/vessel-calls`.
- Support bounded filtering by status, vessel/IMO search, and ETA date range.
- Apply the existing `RequestContext` dependency and tenant predicate to every query.
- Add an explicit development-only seed command that refuses non-development environments.
- Keep the service authoritative; do not copy original Quayt code or introduce auth bypasses.

## Frontend scope

- Replace the post-login empty state with a vessel-call workspace after tenant selection.
- Show summary counts, upcoming calls, status, ETA/ETD, berth, vessel, IMO, and agent.
- Add accessible status/date/search filters, loading, empty, retry, and safe error states.
- Fetch only from the agreed service API; do not ship fake operational data in the clapp.
- Preserve the five-command agent CLI and keep operational data out of CLI snapshots/signals.

## Acceptance gates

- Cross-tenant list, detail, summary, and filter access fail closed.
- Seed data is deterministic, development-only, and contains no real party information.
- API bounds and malformed filters are tested.
- The authenticated desktop renders seeded vessel calls and usable empty/error states.
- Existing Phase 2 authentication, secret-containment, CLI, ClappKit, and migration gates remain green.
- QA, Release, and Security report no unresolved P0/P1 before Phase 3 is called complete.

## Excluded

Create/update/delete workflows, public container tracking, finance, integrations, notifications,
mobile workflows, offline writes, legacy migration, production infrastructure, and publication.
