# Phase 1 QA results

Date: 2026-09-23

## Executed checks

| Surface | Command | Result |
|---|---|---|
| Manifest | `npm run validate:manifest` | Pass |
| Launcher manifest | `clatch validate .` | Pass |
| Web | `npm run build:web` | Pass |
| Rust formatting | `cargo fmt --manifest-path src-tauri/Cargo.toml --check` | Pass |
| Rust build | `cargo build --manifest-path src-tauri/Cargo.toml --locked` | Pass |
| Rust tests | `cargo test --manifest-path src-tauri/Cargo.toml --locked` | Pass: 5 tests |
| Service formatting | `ruff format --check .` | Pass: 9 files |
| Service lint | `ruff check .` | Pass |
| Service typing | `mypy src/quayt_service` | Pass: 6 source files |
| Service tests | `pytest -q` | Pass: 13 tests |
| QA acceptance | `python3 qa/phase1_acceptance.py` | Pass |

## Blocking defects

None. The service dependency graph is pinned in `service/requirements.lock`, CI installs
that lock before the editable package with `--no-deps`, and CI runs the dependency-free
QA acceptance checker. The authoritative `clatch validate .` command also passes locally;
CI uses the manifest and QA contract checks because Clatch has no trusted CI installer.

## Residual risk

- Rust reports that transitive crate `block 0.1.6` contains code rejected by a future Rust version. The pinned Phase 1 compiler builds and tests successfully; dependency remediation remains release-owned.
