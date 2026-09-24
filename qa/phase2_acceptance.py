#!/usr/bin/env python3
"""Dependency-free Phase 2 release-contract and test-evidence checks."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_COMMANDS = ["status", "show", "hide", "quit", "ping"]
GUI_AUTH_COMMANDS = {"login", "loginCheck", "cancelLogin", "selectTenant", "logout"}
FORBIDDEN_SNAPSHOT_TERMS = {
    "access_token", "authorization_code", "cookie", "credential", "nonce",
    "password", "refresh_token", "secret", "token", "verifier",
}
REQUIRED_FILES = (
    ".env.example", "compose.dev.yml", "clatch.json", "docs/phase-2.md",
    "docs/release/phase-2-development.md", "service/alembic.ini",
    "service/migrations/env.py", "service/migrations/versions/0001_auth_sessions.py",
    "service/requirements.lock", "service/tests/test_migrations.py",
    "service/tests/test_oidc.py", "src-tauri/src/auth.rs", "src-tauri/src/cli.rs",
    "src-tauri/src/state.rs", "src/types.ts",
)
LOCKED_SERVICE_DEPENDENCIES = ("alembic==", "psycopg==", "SQLAlchemy==", "PyJWT==")


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def check(failures: list[str], condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def rust_struct(source: str, name: str) -> str:
    match = re.search(rf"struct\s+{re.escape(name)}\s*\{{(.*?)\n\}}", source, re.S)
    return match.group(1) if match else ""


def rust_test_source() -> str:
    sections: list[str] = []
    for path in sorted((ROOT / "src-tauri" / "src").glob("*.rs")):
        source = path.read_text(encoding="utf-8")
        marker = source.find("#[cfg(test)]")
        if marker >= 0:
            sections.append(source[marker:])
    return "\n".join(sections)


def python_test_source() -> str:
    paths = sorted((ROOT / "service" / "tests").glob("test_*.py"))
    paths.extend(sorted((ROOT / "qa").glob("test_*.py")))
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def main() -> int:
    failures: list[str] = []
    missing = [path for path in REQUIRED_FILES if not (ROOT / path).is_file()]
    check(failures, not missing, f"missing required files: {', '.join(missing)}")
    if missing:
        raise AssertionError("; ".join(failures))

    manifest = json.loads(read("clatch.json"))
    connector = manifest["connector"]
    manifest_commands = [item["name"] for item in connector["commands"]]
    check(failures, manifest_commands == EXPECTED_COMMANDS, "manifest CLI commands changed")
    check(failures, connector.get("signals") == [], "Phase 2 must declare no signals")
    check(
        failures,
        GUI_AUTH_COMMANDS.isdisjoint(manifest_commands),
        "GUI authentication command is exposed in manifest command names",
    )

    cli = read("src-tauri/src/cli.rs")
    public = re.search(r"PUBLIC_COMMANDS:\s*\[&str;\s*\d+\]\s*=\s*\[(.*?)\];", cli, re.S)
    rust_commands = re.findall(r'"([A-Za-z][A-Za-z0-9]*)"', public.group(1)) if public else []
    help_function = re.search(r"fn print_help\(\)\s*\{(.*?)\n\}", cli, re.S)
    help_commands = (
        re.findall(r"(?:^|\\n)\s{2}quayt ([A-Za-z][A-Za-z0-9]*)", help_function.group(1))
        if help_function
        else []
    )
    check(failures, rust_commands == manifest_commands, "Rust CLI differs from manifest commands")
    check(failures, help_commands == manifest_commands, "CLI help differs from manifest commands")

    state = read("src-tauri/src/state.rs")
    expected_rust_fields = {
        "Snapshot": ["ok", "rev", "busy", "connection", "authentication", "tenants", "issue"],
        "ConnectionStatus": ["status", "service_url", "summary"],
        "AuthenticationStatus": ["status", "summary"],
        "TenantStatus": ["active", "available", "selection_required"],
        "SafeIssue": ["code", "summary"],
    }
    snapshot_declarations = ""
    for name, expected in expected_rust_fields.items():
        body = rust_struct(state, name)
        fields = re.findall(r"^\s+([a-z_]+):", body, re.M)
        check(failures, fields == expected, f"Rust {name} schema changed: {fields}")
        snapshot_declarations += body.lower()
    check(
        failures,
        not any(term in snapshot_declarations for term in FORBIDDEN_SNAPSHOT_TERMS),
        "Rust snapshot schema contains a secret-bearing field",
    )

    types = read("src/types.ts")
    ts_snapshot = re.search(r"export type Snapshot\s*=\s*\{(.*?)^\};", types, re.S | re.M)
    ts_body = ts_snapshot.group(1) if ts_snapshot else ""
    ts_fields = re.findall(r"^  ([A-Za-z][A-Za-z0-9]*):", ts_body, re.M)
    check(failures, ts_fields == expected_rust_fields["Snapshot"], "TypeScript snapshot schema changed")
    check(
        failures,
        not any(term in ts_body.lower() for term in FORBIDDEN_SNAPSHOT_TERMS),
        "TypeScript snapshot schema contains a secret-bearing field",
    )

    models = read("service/src/quayt_service/models.py")
    auth = read("service/src/quayt_service/auth.py")
    tenant_field_start = models.find("selected_tenant_id: Mapped[Optional[UUID]]")
    tenant_field_end = models.find("oidc_token_digest:", tenant_field_start)
    tenant_field = (
        models[tenant_field_start:tenant_field_end]
        if tenant_field_start >= 0 and tenant_field_end > tenant_field_start
        else ""
    )
    check(
        failures,
        "mapped_column" in tenant_field
        and 'ForeignKey("tenants.id", ondelete="SET NULL")' in tenant_field
        and "default=" not in tenant_field,
        "device session tenant must remain nullable with no default tenant",
    )
    check(
        failures,
        re.search(
            r"if view\.selected_tenant_id is None:\s*raise tenant_access_denied\(\)", auth
        )
        is not None,
        "request context no longer denies a missing selected tenant",
    )
    check(
        failures,
        all(token in state for token in (
            "active: None", "available: Vec::new()", "selection_required: false"
        )),
        "client snapshot no longer starts without an active/default tenant",
    )

    python_tests = python_test_source()
    rust_tests = rust_test_source()
    oidc_evidence = {
        "missing": "test_oidc_rejects_missing" in python_tests or "test_missing_oidc" in python_tests,
        "malformed": "not-a-jwt" in python_tests or "malformed" in python_tests,
        "expired": "expired" in python_tests or ('"exp"' in python_tests and "timedelta" in python_tests),
        "wrong signature": "wrong_signature" in python_tests or "attacker" in python_tests,
        "wrong issuer": '"iss": "https://wrong' in python_tests or "wrong_issuer" in python_tests,
        "wrong audience": '"aud": "wrong' in python_tests or "wrong_audience" in python_tests,
        "disallowed algorithm": "HS256" in python_tests or "disallowed_algorithm" in python_tests,
    }
    absent_oidc = [name for name, present in oidc_evidence.items() if not present]
    check(failures, not absent_oidc, f"OIDC negative matrix lacks: {', '.join(absent_oidc)}")
    check(
        failures,
        all(marker in python_tests for marker in (
            "PostgresSessionRepository", "QUAYT_DATABASE_URL", "alembic"
        )),
        "no PostgreSQL-backed migration/session integration test is present",
    )

    rust_test_names = re.findall(r"fn\s+([a-z][a-z0-9_]+)\s*\(", rust_tests)
    check(
        failures, any("timeout" in name for name in rust_test_names),
        "PKCE callback timeout test is absent",
    )
    check(
        failures, any("cancel" in name for name in rust_test_names),
        "PKCE callback cancellation test is absent",
    )
    check(
        failures,
        any("replay" in name or "single_use" in name for name in rust_test_names),
        "PKCE state/callback replay test is absent",
    )

    all_tests = f"{python_tests}\n{rust_tests}".lower()
    check(failures, "sentinel" in all_tests, "sentinel-secret propagation test is absent")
    check(
        failures,
        "sentinel" in all_tests
        and all(term in all_tests for term in ("snapshot", "cli", "signal"))
        and ("log" in all_tests or "error" in all_tests),
        "sentinel test does not cover snapshots, CLI, signals, and loggable/error output",
    )

    product_source = "\n".join(
        path.read_text(encoding="utf-8")
        for directory in (ROOT / "src", ROOT / "src-tauri" / "src")
        for path in sorted(directory.iterdir())
        if path.suffix in {".rs", ".ts", ".tsx"}
    )
    check(
        failures,
        "app.toAgent" not in product_source and "control.emit" not in product_source,
        "product source emits a Clatch signal",
    )

    lock = read("service/requirements.lock")
    missing_dependencies = [item for item in LOCKED_SERVICE_DEPENDENCIES if item not in lock]
    check(failures, not missing_dependencies, f"service lock misses: {', '.join(missing_dependencies)}")
    migration = read("service/migrations/versions/0001_auth_sessions.py")
    check(
        failures,
        re.search(r'^revision(?:\s*:\s*str)?\s*=\s*"[^"]+"', migration, re.M) is not None,
        "baseline migration has no revision",
    )
    check(failures, "down_revision" in migration, "baseline migration has no parent declaration")

    compose = read("compose.dev.yml")
    check(failures, "postgres:17.5-bookworm" in compose, "development PostgreSQL image is not pinned")
    check(failures, "127.0.0.1:54329:5432" in compose, "development PostgreSQL is not loopback-only")
    environment = read(".env.example")
    check(failures, "QUAYT_ENV=development" in environment, "development environment is not explicit")
    check(failures, "REPLACE_WITH_" in environment, "environment template lacks secret placeholders")

    if failures:
        raise AssertionError("; ".join(failures))
    print("Phase 2 acceptance contract: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, OSError, TypeError, ValueError) as error:
        print(f"Phase 2 acceptance contract: FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
