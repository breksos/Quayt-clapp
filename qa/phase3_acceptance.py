#!/usr/bin/env python3
"""Dependency-free Phase 3 vessel-call release-contract evidence checks."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = ["status", "show", "hide", "quit", "ping"]
REQUIRED = (
    "docs/phase-3.md", "service/migrations/versions/0002_vessel_calls.py",
    "service/src/quayt_service/seed_vessel_calls.py",
    "service/src/quayt_service/vessel_call_routes.py",
    "service/src/quayt_service/vessel_calls.py",
    "service/tests/test_vessel_calls.py", "service/tests/test_vessel_call_security.py",
    "service/tests/test_postgres_vessel_calls.py",
    "src-tauri/src/auth.rs", "src-tauri/src/cli.rs", "src-tauri/src/state.rs",
    "src-tauri/src/state/vessel_response_tests.rs",
    "src/App.tsx", "src/types.ts",
)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def check(failures: list[str], condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    missing = [path for path in REQUIRED if not (ROOT / path).is_file()]
    check(failures, not missing, f"missing Phase 3 files: {', '.join(missing)}")
    if missing:
        raise AssertionError("; ".join(failures))

    manifest = json.loads(read("clatch.json"))
    commands = [item["name"] for item in manifest["connector"]["commands"]]
    check(failures, commands == COMMANDS, "Phase 3 changed the five-command agent CLI")
    check(failures, manifest["connector"].get("signals") == [], "Phase 3 declares signals")
    cli = read("src-tauri/src/cli.rs")
    declared = re.search(r"PUBLIC_COMMANDS:\s*\[&str;\s*\d+\]\s*=\s*\[(.*?)\];", cli, re.S)
    rust_commands = re.findall(r'"([a-z]+)"', declared.group(1)) if declared else []
    help_block = re.search(r"fn print_help\(\)\s*\{(.*?)\n\}", cli, re.S)
    help_commands = re.findall(r"(?:^|\\n)\s{2}quayt ([a-z]+)", help_block.group(1)) if help_block else []
    check(failures, rust_commands == commands, "Rust CLI command set differs from manifest")
    check(failures, help_commands == commands, "CLI help differs from manifest")

    routes = read("service/src/quayt_service/vessel_call_routes.py")
    for route_name in ("list_vessel_calls", "vessel_call_summary", "get_vessel_call"):
        block = re.search(rf"def {route_name}\(.*?(?=\n\n@|\Z)", routes, re.S)
        check(failures, block is not None and "Depends(require_request_context)" in block.group(0),
              f"{route_name} lacks RequestContext enforcement")
    check(failures,
          "repository.list(context.tenant.tenant_id, filters)" in routes
          and "repository.summary(context.tenant.tenant_id)" in routes
          and "repository.get(context.tenant.tenant_id, vessel_call_id)" in routes,
          "vessel-call routes do not pass verified tenant to every query")

    model = read("service/src/quayt_service/vessel_calls.py")
    check(failures,
          model.count("VesselCall.tenant_id == tenant_id") >= 3,
          "PostgreSQL vessel-call query lacks a tenant predicate")
    for bound in ("max_length=100", "le=100", "le=10_000", "extra=\"forbid\""):
        check(failures, bound in model, f"vessel-call filter bound missing: {bound}")
    check(failures, "require_utc_input" in model and "ordered_range" in model,
          "vessel-call ETA filters do not enforce timezone and ordering")

    migration = read("service/migrations/versions/0002_vessel_calls.py")
    check(failures, 'revision = "0002_vessel_calls"' in migration, "wrong vessel-call migration revision")
    check(failures, 'down_revision = "0001_auth_sessions"' in migration,
          "vessel-call migration is not forward from Phase 2")
    check(failures, 'ForeignKey("tenants.id", ondelete="CASCADE")' in migration
          and "ix_vessel_calls_tenant_eta" in migration
          and "ix_vessel_calls_tenant_status_eta" in migration,
          "vessel-call migration lacks tenant ownership or tenant query indexes")
    check(
        failures,
        "ENABLE ROW LEVEL SECURITY" in migration
        and "FORCE ROW LEVEL SECURITY" in migration
        and "CREATE POLICY vessel_calls_tenant_isolation" in migration
        and "NOLOGIN NOSUPERUSER NOBYPASSRLS" in migration,
        "vessel-call migration lacks forced RLS or a restricted runtime role",
    )
    check(
        failures,
        "current_setting('quayt.tenant_id', true)" in migration
        and "WITH CHECK" in migration,
        "vessel-call RLS policy does not fail closed for tenant context",
    )
    check(
        failures,
        "SET LOCAL ROLE" in model
        and "set_config(:setting, :tenant_id, true)" in model
        and "row_security_active" in model,
        "repository does not transaction-scope the restricted role and tenant context",
    )

    seed = read("service/src/quayt_service/seed_vessel_calls.py")
    check(failures, "settings.environment is not Environment.DEVELOPMENT" in seed
          and "allowed only in development" in seed, "seed does not fail closed outside development")
    check(failures, "uuid5(SEED_NAMESPACE" in seed and "datetime(2030, 1, 1" in seed
          and "on_conflict_do_update" in seed, "seed is not deterministic and idempotent")
    check(failures, "Quayt Demo Agency" in seed, "seed lacks synthetic fixture identity")

    service_tests = (
        read("service/tests/test_vessel_calls.py")
        + read("service/tests/test_vessel_call_security.py")
        + read("service/tests/test_postgres_vessel_calls.py")
    )
    for evidence in (
        "test_api_isolates_tenants_filters_and_hides_foreign_ids",
        "test_filter_bounds_and_time_validation",
        "test_seed_refuses_non_development_without_opening_database",
        "test_postgres_tenant_isolation_filters_and_deterministic_seed",
        "test_postgres_rls_fails_closed_for_missing_malformed_and_foreign_context",
        "test_pool_reuse_clears_role_and_tenant_after_commit_and_rollback",
        "test_runtime_role_cannot_disable_or_bypass_policy",
        "test_migrated_rls_and_runtime_role_attributes",
    ):
        check(failures, evidence in service_tests, f"required service evidence missing: {evidence}")
    for assertion in ("foreign.status_code == 404", 'summary.json()["total"] == 1',
                      "limit=101", "offset=10001", "eta_from=2030-01-01T00:00:00"):
        check(failures, assertion in service_tests, f"negative assertion missing: {assertion}")

    state, types, frontend = read("src-tauri/src/state.rs"), read("src/types.ts"), read("src/App.tsx")
    check(failures, "struct GuiSnapshot" in state and "vessel_calls: VesselCallStatus" in state,
          "GUI vessel-call state projection is absent")
    check(failures, "operational_vessel_calls_stay_out_of_cli_projection" in state
          and "sentinel-vessel-name" in state, "CLI operational-data exclusion test is absent")
    check(failures, "vessel_call_filters_are_bounded_and_date_shaped" in state,
          "desktop filter bounds test is absent")
    delayed = read("src-tauri/src/state/vessel_response_tests.rs")
    check(
        failures,
        "self.receive_vessel_calls(" in state
        and "self.receive_vessel_detail(" in state
        and "delayed_list_and_summary_success_and_error_cannot_cross_workspace_boundaries" in delayed
        and "delayed_detail_success_and_error_cannot_cross_workspace_boundaries" in delayed
        and "actual_workspace_commands_clear_data_before_waiting_for_an_operation" in delayed,
        "delayed tenant-switch responses do not exercise production vessel-call commit paths",
    )
    check(failures, "vesselCalls: VesselCalls" in types
          and 'status: "idle" | "loading" | "ready" | "empty" | "error"' in types,
          "TypeScript vessel-call workspace state is incomplete")
    for marker in ('aria-label="Vessel-call summary"', 'htmlFor="call-search"',
                   'htmlFor="call-status"', 'type="date"', "Loading vessel calls",
                   "No vessel calls match these filters", "Retry"):
        check(failures, marker in frontend, f"accessible vessel-call state/control missing: {marker}")

    product = "\n".join(
        path.read_text(encoding="utf-8")
        for directory in (ROOT / "src", ROOT / "src-tauri" / "src")
        for path in sorted(directory.iterdir())
        if path.suffix in {".rs", ".ts", ".tsx"}
    )
    check(failures, "app.toAgent" not in product and "control.emit" not in product,
          "Phase 3 product code emits a Clatch signal")
    if failures:
        raise AssertionError("; ".join(failures))
    print("Phase 3 acceptance contract: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, OSError, TypeError, ValueError) as error:
        print(f"Phase 3 acceptance contract: FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
