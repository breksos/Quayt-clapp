# Phase 1 QA acceptance

Run the dependency-free acceptance checker from the repository root:

```sh
python3 qa/phase1_acceptance.py
```

The checker fails closed when:

- a required Phase 1 project file is absent, the Python service has no committed dependency lock, or ClappKit is no longer declared as a submodule;
- manifest commands, Rust public dispatch, and CLI help differ from `status`, `show`, `hide`, `quit`, and `ping`;
- the manifest or product source exposes a signal API;
- the Rust and TypeScript snapshots differ, contain fields beyond connection status, or expose a secret-capable field;
- the service tenant/request context supplies an implicit tenant or omits nil-tenant rejection.

The checker complements, and does not replace, the Rust, web, service, and launcher manifest checks documented for contributors.
