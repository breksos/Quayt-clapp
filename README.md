# Quayt clapp

Quayt is a ClappKit desktop terminal for port and terminal operations, backed by a separate
authoritative service. Phase 2 adds OIDC browser sign-in, revocable device sessions, and
server-verified tenant selection. It still exposes no port-operation data or mutation.

ClappKit is included as the `clappkit/` git submodule. Clone with:

```sh
git clone --recurse-submodules <repository-url>
```

Install dependencies and build the host binary with `npm install` and `npm run build`. The build
stages the single Rust executable at `bin/quayt`, where the Clatch manifest expects it. Development
service setup and PostgreSQL commands are in `docs/release/phase-2-development.md`.

The agent CLI surface remains exactly `quayt status`, `show`, `hide`, `quit`, and `ping`.
Authentication stays in the human window, credentials stay in the OS credential store, and Phase 2
emits no signals.
