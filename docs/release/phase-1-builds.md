# Phase 1 build checks

Phase 1 CI is build-only. It does not package, sign, upload, deploy, or publish an
artifact. GitHub Actions uses Node 22.14.0, Rust 1.85.1, Python 3.11.11, and pip 25.0.1.

Run these checks from the repository root after cloning with the `clappkit` submodule:

```sh
npm ci
npm run validate:manifest
# Clatch has no trusted CI installer. Run this locally with a trusted Clatch installation.
clatch validate .
python3 qa/phase1_acceptance.py
npm run build:web
cargo fetch --manifest-path src-tauri/Cargo.toml --locked
cargo fmt --manifest-path src-tauri/Cargo.toml --check
cargo build --manifest-path src-tauri/Cargo.toml --locked
cargo test --manifest-path src-tauri/Cargo.toml --locked
python3.11 -m venv service/.venv
service/.venv/bin/python -m pip install --upgrade pip==25.0.1
service/.venv/bin/python -m pip install -r service/requirements.lock
service/.venv/bin/python -m pip install -e './service[dev]' --no-deps
service/.venv/bin/ruff format --check service
service/.venv/bin/ruff check service
service/.venv/bin/mypy --package quayt_service
service/.venv/bin/pytest service
```

The committed `package-lock.json`, `src-tauri/Cargo.lock`, and `service/requirements.lock`
make the Node, Rust, and Python dependency graphs reproducible. CI installs the Python
lock before the editable service package and uses `--no-deps` so the editable install
does not resolve dependency ranges.

CI runs the repository manifest validator and the dependency-free Phase 1 acceptance
contract (`python3 qa/phase1_acceptance.py`). Clatch has no trusted CI installer, so
`clatch validate .` remains a required local gate using a trusted Clatch installation.
