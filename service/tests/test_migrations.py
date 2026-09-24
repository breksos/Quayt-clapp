from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from quayt_service.models import Base
from quayt_service.repository import MIGRATION_HEAD


def test_migration_graph_has_one_expected_head() -> None:
    service_root = Path(__file__).parents[1]
    config = Config(str(service_root / "alembic.ini"))
    config.set_main_option("script_location", str(service_root / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == [MIGRATION_HEAD]


def test_model_contains_only_phase_two_authoritative_tables() -> None:
    assert set(Base.metadata.tables) == {
        "actors",
        "tenants",
        "memberships",
        "device_sessions",
        "credential_generations",
        "auth_audit",
    }
