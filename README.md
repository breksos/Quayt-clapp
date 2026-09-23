# Quayt clapp

Quayt is a ClappKit desktop terminal for port and terminal operations, backed by a central authoritative service. Phase 1 provides the native desktop shell and a shared, revisioned connection-status snapshot; it does not yet connect to the service or store credentials and operational state.

ClappKit is included as the `clappkit/` git submodule. Clone with:

```sh
git clone --recurse-submodules <repository-url>
```

Install dependencies and build the host binary with `npm install` and `npm run build`. The build stages the single Rust executable at `bin/quayt`, where the Clatch manifest expects it. Run `npm test`, `npm run validate:manifest`, and `clatch validate .` for focused verification.

The agent CLI surface is exactly `quayt status`, `show`, `hide`, `quit`, and `ping`. Phase 1 emits no signals.
