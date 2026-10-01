"""Development-only OIDC-compatible demo tenant provisioning command."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid5

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from quayt_service.database import create_database_engine, create_session_factory
from quayt_service.models import Actor, Membership, Tenant
from quayt_service.seed_vessel_calls import seed_vessel_calls
from quayt_service.settings import Environment, Settings, SettingsError

DEMO_NAMESPACE = UUID("807ad1b7-69a2-45d1-a49f-4d9957af0da8")
DEMO_TIME = datetime(2030, 1, 1, tzinfo=timezone.utc)
DEFAULT_DISPLAY_NAME = "Quayt Demo Operator"
DEFAULT_TENANT_NAME = "Quayt Demo Terminal"
DEMO_ROLES = ["operator"]


@dataclass(frozen=True)
class DemoProvisioningResult:
    actor_id: UUID
    tenant_id: UUID
    membership_id: UUID
    vessel_call_count: int


def _require_development(settings: Settings) -> None:
    if settings.environment is not Environment.DEVELOPMENT:
        raise SettingsError("demo provisioning is allowed only in development")


def provision_demo_identity(
    settings: Settings,
    subject: str,
    sessions: sessionmaker[Session],
    *,
    display_name: str = DEFAULT_DISPLAY_NAME,
    tenant_name: str = DEFAULT_TENANT_NAME,
) -> tuple[UUID, UUID, UUID]:
    """Upsert an active actor, tenant, and membership for a real OIDC subject."""
    _require_development(settings)
    subject = subject.strip()
    display_name = display_name.strip()
    tenant_name = tenant_name.strip()
    if not subject or len(subject) > 512:
        raise ValueError("subject must contain between 1 and 512 characters")
    if not display_name or len(display_name) > 255:
        raise ValueError("display name must contain between 1 and 255 characters")
    if not tenant_name or len(tenant_name) > 255:
        raise ValueError("tenant name must contain between 1 and 255 characters")
    if settings.auth_issuer is None:
        raise SettingsError("QUAYT_AUTH_ISSUER is required")

    actor_id = uuid5(DEMO_NAMESPACE, f"actor:{settings.auth_issuer}:{subject}")
    tenant_id = uuid5(DEMO_NAMESPACE, "tenant")
    with sessions.begin() as session:
        persisted_actor_id = session.scalar(
            insert(Actor)
            .values(
                id=actor_id,
                issuer=settings.auth_issuer,
                subject=subject,
                display_name=display_name,
                status="active",
                created_at=DEMO_TIME,
                updated_at=DEMO_TIME,
            )
            .on_conflict_do_update(
                constraint="uq_actors_issuer_subject",
                set_={
                    "display_name": display_name,
                    "status": "active",
                    "updated_at": DEMO_TIME,
                },
            )
            .returning(Actor.id)
        )
        if persisted_actor_id is None:
            raise RuntimeError("demo actor upsert returned no identifier")
        actor_id = persisted_actor_id
        membership_id = uuid5(DEMO_NAMESPACE, f"membership:{actor_id}:{tenant_id}")
        session.execute(
            insert(Tenant)
            .values(
                id=tenant_id,
                name=tenant_name,
                status="active",
                created_at=DEMO_TIME,
                updated_at=DEMO_TIME,
            )
            .on_conflict_do_update(
                index_elements=[Tenant.id],
                set_={"name": tenant_name, "status": "active", "updated_at": DEMO_TIME},
            )
        )
        session.execute(
            insert(Membership)
            .values(
                id=membership_id,
                actor_id=actor_id,
                tenant_id=tenant_id,
                status="active",
                roles=DEMO_ROLES,
                version=1,
                created_at=DEMO_TIME,
                updated_at=DEMO_TIME,
            )
            .on_conflict_do_update(
                constraint="uq_memberships_actor_tenant",
                set_={"status": "active", "roles": DEMO_ROLES, "updated_at": DEMO_TIME},
            )
        )
    return actor_id, tenant_id, membership_id


def provision_demo(
    settings: Settings,
    subject: str,
    sessions: sessionmaker[Session],
    *,
    display_name: str = DEFAULT_DISPLAY_NAME,
    tenant_name: str = DEFAULT_TENANT_NAME,
) -> DemoProvisioningResult:
    _require_development(settings)
    actor_id, tenant_id, membership_id = provision_demo_identity(
        settings,
        subject,
        sessions,
        display_name=display_name,
        tenant_name=tenant_name,
    )
    count = seed_vessel_calls(settings, tenant_id, sessions)
    return DemoProvisioningResult(actor_id, tenant_id, membership_id, count)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Provision deterministic demo access for an existing OIDC subject."
    )
    parser.add_argument(
        "--subject", required=True, help="Exact OIDC subject claim for the demo user"
    )
    parser.add_argument("--display-name", default=DEFAULT_DISPLAY_NAME)
    parser.add_argument("--tenant-name", default=DEFAULT_TENANT_NAME)
    args = parser.parse_args()
    settings = Settings.from_env()
    _require_development(settings)
    assert settings.database_url is not None
    sessions = create_session_factory(create_database_engine(settings.database_url))
    result = provision_demo(
        settings,
        args.subject,
        sessions,
        display_name=args.display_name,
        tenant_name=args.tenant_name,
    )
    print(
        f"provisioned demo actor={result.actor_id} tenant={result.tenant_id} "
        f"membership={result.membership_id} vessel_calls={result.vessel_call_count}"
    )


if __name__ == "__main__":
    main()
