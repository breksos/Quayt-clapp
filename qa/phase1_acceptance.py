#!/usr/bin/env python3
"""Dependency-free Phase 1 contract checks."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_COMMANDS = ["status", "show", "hide", "quit", "ping"]
REQUIRED_FILES = [
    ".gitmodules",
    "AGENTS.md",
    "README.md",
    "assets/icon.png",
    "assets/icon.svg",
    "clatch.json",
    "package-lock.json",
    "package.json",
    "run.cmd",
    "run.sh",
    "clappkit/Cargo.toml",
    "clappkit/Cargo.lock",
    "docs/phase-1.md",
    "docs/security/phase-1-checklist.md",
    "src-tauri/Cargo.toml",
    "src-tauri/Cargo.lock",
    "src-tauri/tauri.conf.json",
    "src-tauri/src/app.rs",
    "src-tauri/src/cli.rs",
    "src-tauri/src/lib.rs",
    "src-tauri/src/main.rs",
    "src-tauri/src/state.rs",
    "src/App.tsx",
    "src/types.ts",
    "service/pyproject.toml",
    "service/src/quayt_service/context.py",
    "service/tests/test_context.py",
]
SERVICE_LOCK_FILES = [
    "service/requirements.lock",
    "service/uv.lock",
    "service/poetry.lock",
    "service/Pipfile.lock",
]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def main() -> int:
    missing = [path for path in REQUIRED_FILES if not (ROOT / path).is_file()]
    require(not missing, f"missing required files: {', '.join(missing)}")
    require(
        any((ROOT / path).is_file() for path in SERVICE_LOCK_FILES),
        "Python service has no committed dependency lock",
    )

    manifest = json.loads(read("clatch.json"))
    require(manifest["id"] == "com.arfium.quayt", "unexpected app identity")
    require(manifest["connector"]["cli"] == "quayt", "unexpected CLI name")
    commands = [item["name"] for item in manifest["connector"]["commands"]]
    require(commands == EXPECTED_COMMANDS, "manifest commands differ from Phase 1")
    require(manifest["connector"].get("signals") == [], "Phase 1 must declare no signals")

    cli_source = read("src-tauri/src/cli.rs")
    match = re.search(r"PUBLIC_COMMANDS:\s*\[&str;\s*5\]\s*=\s*\[(.*?)\];", cli_source, re.S)
    require(match is not None, "Rust CLI command declaration not found")
    rust_commands = re.findall(r'"([a-z]+)"', match.group(1))
    require(rust_commands == EXPECTED_COMMANDS, "Rust CLI and manifest commands differ")
    for command in EXPECTED_COMMANDS:
        require(f"quayt {command}" in cli_source, f"CLI help omits {command}")

    state_source = read("src-tauri/src/state.rs")
    snapshot = re.search(r"struct Snapshot\s*\{(.*?)\n\}", state_source, re.S)
    require(snapshot is not None, "Snapshot declaration not found")
    fields = re.findall(r"^\s*([a-z_]+):", snapshot.group(1), re.M)
    require(
        fields == ["ok", "rev", "busy", "connection", "authentication", "tenants", "issue"],
        "safe snapshot exposes unexpected fields",
    )
    secret_terms = ("password", "secret", "token", "credential", "authorization", "cookie")
    require(not any(term in snapshot.group(1).lower() for term in secret_terms), "snapshot may expose a secret")
    connection = re.search(r"struct ConnectionStatus\s*\{(.*?)\n\}", state_source, re.S)
    require(connection is not None, "ConnectionStatus declaration not found")
    connection_fields = re.findall(r"^\s*([a-z_]+):", connection.group(1), re.M)
    require(
        connection_fields == ["status", "service_url", "summary"],
        "connection status exposes unexpected fields",
    )
    require(not any(term in connection.group(1).lower() for term in secret_terms),
            "connection status may expose a secret")
    types_source = read("src/types.ts")
    types_snapshot = re.search(r"export type Snapshot\s*=\s*\{(.*?)\n\};", types_source, re.S)
    require(types_snapshot is not None, "TypeScript Snapshot declaration not found")
    type_fields = re.findall(r"^  ([A-Za-z][A-Za-z0-9]*):", types_snapshot.group(1), re.M)
    require(
        type_fields == [
            "ok", "rev", "busy", "connection", "authentication", "tenants", "issue", "vesselCalls"
        ],
        "TypeScript GUI snapshot differs from the safe projection contract",
    )
    require("serviceUrl: string | null;" in types_snapshot.group(1),
            "TypeScript connection status shape is unexpected")
    require(not any(term in types_snapshot.group(1).lower() for term in secret_terms),
            "TypeScript snapshot may expose a secret")
    product_source = "\n".join(
        path.read_text(encoding="utf-8")
        for directory in (ROOT / "src", ROOT / "src-tauri" / "src")
        for path in directory.iterdir()
        if path.suffix in {".rs", ".ts", ".tsx"}
    )
    require("app.toAgent" not in product_source and "control.emit" not in product_source,
            "Phase 1 product source emits a signal")

    context_source = read("service/src/quayt_service/context.py")
    context_tests = read("service/tests/test_context.py")
    require(re.search(r"class TenantContext\(BaseModel\):.*?tenant_id:\s*UUID", context_source, re.S) is not None,
            "TenantContext must require tenant_id")
    require("UUID(int=0)" in context_tests, "nil tenant rejection lacks a test")
    require("tenant_id cannot be nil" in context_source, "nil tenant is not rejected")
    request_context = re.search(r"class RequestContext\(BaseModel\):(.*)", context_source, re.S)
    require(request_context is not None, "RequestContext declaration not found")
    require(re.search(r"^\s*tenant:\s*TenantContext\s*$", request_context.group(1), re.M) is not None,
            "RequestContext must require tenant context")
    require(re.search(r"^\s*actor:\s*ActorContext\s*$", request_context.group(1), re.M) is not None,
            "RequestContext must require actor context")

    submodule = subprocess.run(
        ["git", "submodule", "status", "clappkit"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    require(not submodule.startswith(("-", "+", "U")), "ClappKit submodule is missing or modified")

    print("Phase 1 acceptance contract: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, OSError, subprocess.CalledProcessError) as error:
        print(f"Phase 1 acceptance contract: FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
