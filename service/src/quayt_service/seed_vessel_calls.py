"""Deterministic development-only vessel-call seed command."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from quayt_service.database import create_database_engine, create_session_factory
from quayt_service.models import Tenant, VesselCall
from quayt_service.settings import Environment, Settings, SettingsError
from quayt_service.vessel_calls import set_tenant_context

SEED_NAMESPACE = UUID("78efeafb-25e8-42b2-b4dd-c6fe668acd8a")
SEED_ROWS = (
    ("Northstar", "9000001", "expected", "2030-01-10T08:00:00+00:00", None, "A1"),
    ("Harbor Light", "9000002", "arrived", "2030-01-11T09:30:00+00:00", None, "B2"),
    (
        "Meridian",
        "9000003",
        "berthed",
        "2030-01-12T11:00:00+00:00",
        "2030-01-13T16:00:00+00:00",
        "C3",
    ),
)


def seed_vessel_calls(settings: Settings, tenant_id: UUID, sessions: sessionmaker[Session]) -> int:
    if settings.environment is not Environment.DEVELOPMENT:
        raise SettingsError("vessel-call seed is allowed only in development")
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    with sessions.begin() as session:
        tenant = session.scalar(
            select(Tenant).where(Tenant.id == tenant_id, Tenant.status == "active")
        )
        if tenant is None:
            raise ValueError("active tenant not found")
        set_tenant_context(session, tenant_id)
        for name, imo, status, eta, etd, berth in SEED_ROWS:
            values = {
                "id": uuid5(SEED_NAMESPACE, f"{tenant_id}:{imo}"),
                "tenant_id": tenant_id,
                "vessel_name": name,
                "imo_number": imo,
                "agent_name": "Quayt Demo Agency",
                "berth": berth,
                "status": status,
                "eta": datetime.fromisoformat(eta),
                "etd": None if etd is None else datetime.fromisoformat(etd),
                "created_at": now,
                "updated_at": now,
            }
            statement = insert(VesselCall).values(**values)
            session.execute(
                statement.on_conflict_do_update(
                    index_elements=[VesselCall.id],
                    set_={key: value for key, value in values.items() if key != "id"},
                )
            )
    return len(SEED_ROWS)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-id", type=UUID, required=True)
    args = parser.parse_args()
    settings = Settings.from_env()
    assert settings.database_url is not None
    sessions = create_session_factory(create_database_engine(settings.database_url))
    count = seed_vessel_calls(settings, args.tenant_id, sessions)
    print(f"seeded {count} vessel calls")


if __name__ == "__main__":
    main()
