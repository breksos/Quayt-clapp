# Phase 2 QA results

Status: **PASS locally; PostgreSQL CI evidence pending**

The dependency-free Phase 2 acceptance checker gates:

- exact manifest, Rust CLI, and help command alignment;
- absence of GUI authentication commands and Clatch signals from the agent surface;
- non-secret Rust and TypeScript snapshot schemas with no default tenant;
- complete OIDC denial, PostgreSQL integration, PKCE lifecycle, and sentinel-secret test evidence;
- pinned migration, dependency, and local-development configuration contracts.

Latest local result: the acceptance checker, web build, manifest validation, Rust formatting,
16 Rust tests, Ruff formatting/lint, strict mypy, and 36 service tests passed. Five PostgreSQL
repository integration tests skipped because this host has no PostgreSQL server or container
runtime. The CI service must run those tests plus `alembic upgrade head` and `alembic check`
before Phase 2 can receive its final green status.
