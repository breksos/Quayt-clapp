from __future__ import annotations

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from quayt_service.auth import require_request_context
from quayt_service.database import create_database_engine, create_session_factory
from quayt_service.domain import VerifiedIdentity
from quayt_service.errors import ServiceError
from quayt_service.models import (
    Actor,
    AuthAudit,
    Base,
    CredentialGeneration,
    DeviceSession,
    Membership,
    Tenant,
)
from quayt_service.repository import (
    MIGRATION_HEAD,
    PostgresSessionRepository,
    utc_now,
)
from quayt_service.security import CredentialHasher


@pytest.fixture(scope="module")
def postgres_backend() -> Iterator[tuple[Engine, sessionmaker[Session], PostgresSessionRepository]]:
    database_url = os.environ.get("QUAYT_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("QUAYT_DATABASE_URL is not set")
    engine = create_database_engine(database_url)
    sessions = create_session_factory(engine)
    repository = PostgresSessionRepository(sessions)
    yield engine, sessions, repository
    engine.dispose()


def test_migration_head_and_metadata_have_no_drift(
    postgres_backend: tuple[Engine, sessionmaker[Session], PostgresSessionRepository],
) -> None:
    engine, _, repository = postgres_backend
    assert repository.ready()
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert revision == MIGRATION_HEAD
        context = MigrationContext.configure(
            connection,
            opts={
                "compare_type": True,
                "compare_server_default": True,
            },
        )
        assert compare_metadata(context, Base.metadata) == []


def test_postgres_session_repository_lifecycle(
    postgres_backend: tuple[Engine, sessionmaker[Session], PostgresSessionRepository],
) -> None:
    _, sessions, repository = postgres_backend
    marker = uuid4().hex
    identity = VerifiedIdentity(
        issuer=f"https://issuer.example/{marker}",
        subject=f"subject-{marker}",
        display_name="PostgreSQL Test Operator",
    )
    hasher = CredentialHasher(b"k" * 32)
    now = utc_now()
    expires_at = now + timedelta(hours=1)

    session_id = uuid4()
    initial_token_digest = hasher.token_digest("oidc-access-one")
    credential = hasher.derive_credential(session_id, initial_token_digest)
    credential_hash = hasher.credential_hash(credential)
    created = repository.create_session(
        identity,
        uuid4(),
        session_id,
        credential_hash,
        initial_token_digest,
        expires_at,
        f"create-{marker}",
        5,
    )
    assert created.session_id == session_id
    assert created.memberships == ()
    assert created.selected_tenant_id is None
    assert repository.get_session(session_id, credential_hash) == created

    active_tenant_id = uuid4()
    foreign_tenant_id = uuid4()
    inactive_membership_tenant_id = uuid4()
    inactive_tenant_id = uuid4()
    with sessions.begin() as db:
        db.add_all(
            [
                Tenant(
                    id=active_tenant_id,
                    name=f"Active {marker}",
                    status="active",
                    created_at=now,
                    updated_at=now,
                ),
                Tenant(
                    id=foreign_tenant_id,
                    name=f"Foreign {marker}",
                    status="active",
                    created_at=now,
                    updated_at=now,
                ),
                Tenant(
                    id=inactive_membership_tenant_id,
                    name=f"Inactive membership {marker}",
                    status="active",
                    created_at=now,
                    updated_at=now,
                ),
                Tenant(
                    id=inactive_tenant_id,
                    name=f"Inactive tenant {marker}",
                    status="disabled",
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        db.add_all(
            [
                Membership(
                    id=uuid4(),
                    actor_id=created.actor_id,
                    tenant_id=active_tenant_id,
                    status="active",
                    roles=["operator"],
                    version=1,
                    created_at=now,
                    updated_at=now,
                ),
                Membership(
                    id=uuid4(),
                    actor_id=created.actor_id,
                    tenant_id=inactive_membership_tenant_id,
                    status="disabled",
                    roles=["operator"],
                    version=1,
                    created_at=now,
                    updated_at=now,
                ),
                Membership(
                    id=uuid4(),
                    actor_id=created.actor_id,
                    tenant_id=inactive_tenant_id,
                    status="active",
                    roles=["operator"],
                    version=1,
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )

    visible = repository.get_session(session_id, credential_hash)
    assert visible is not None
    assert [membership.tenant_id for membership in visible.memberships] == [active_tenant_id]

    for denied_tenant in (
        foreign_tenant_id,
        inactive_membership_tenant_id,
        inactive_tenant_id,
    ):
        result, view = repository.select_tenant(
            session_id,
            credential_hash,
            denied_tenant,
            1,
            f"denied-{marker}",
        )
        assert (result, view) == ("denied", None)

    result, selected = repository.select_tenant(
        session_id,
        credential_hash,
        active_tenant_id,
        1,
        f"select-{marker}",
    )
    assert result == "ok"
    assert selected is not None
    assert selected.selected_tenant_id == active_tenant_id

    with sessions.begin() as db:
        membership = db.execute(
            select(Membership).where(
                Membership.actor_id == created.actor_id,
                Membership.tenant_id == active_tenant_id,
            )
        ).scalar_one()
        membership.status = "disabled"
        membership.version += 1
        membership.updated_at = utc_now()

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(session_repository=repository, credential_hasher=hasher)
        )
    )
    with pytest.raises(ServiceError) as denied_context:
        require_request_context(request, f"Session {credential}")
    assert denied_context.value.status_code == 403
    assert denied_context.value.code == "tenant_access_denied"

    with sessions.begin() as db:
        membership = db.execute(
            select(Membership).where(
                Membership.actor_id == created.actor_id,
                Membership.tenant_id == active_tenant_id,
            )
        ).scalar_one()
        membership.status = "active"
        membership.version += 1
        membership.updated_at = utc_now()

    refreshed_token_digest = hasher.token_digest("oidc-access-two")
    rotated_credential = hasher.derive_credential(session_id, refreshed_token_digest)
    rotated_hash = hasher.credential_hash(rotated_credential)
    result, refreshed = repository.refresh(
        identity,
        session_id,
        credential_hash,
        rotated_hash,
        refreshed_token_digest,
        expires_at,
        f"refresh-{marker}",
    )
    assert result == "ok"
    assert refreshed is not None
    assert repository.get_session(session_id, credential_hash) is None
    assert repository.get_session(session_id, rotated_hash) is not None

    result, replayed = repository.refresh(
        identity,
        session_id,
        credential_hash,
        hasher.credential_hash(
            hasher.derive_credential(session_id, hasher.token_digest("oidc-access-three"))
        ),
        hasher.token_digest("oidc-access-three"),
        expires_at,
        f"replay-{marker}",
    )
    assert (result, replayed) == ("replay", None)
    assert repository.get_session(session_id, rotated_hash) is None
    with sessions() as db:
        compromised = db.get(DeviceSession, session_id)
        assert compromised is not None
        assert compromised.revoke_reason == "credential_replay"
        statuses = set(
            db.scalars(
                select(CredentialGeneration.status).where(
                    CredentialGeneration.session_id == session_id
                )
            )
        )
        assert statuses == {"revoked"}

    logout_session_id = uuid4()
    logout_token_digest = hasher.token_digest("oidc-access-four")
    logout_credential = hasher.derive_credential(logout_session_id, logout_token_digest)
    logout_hash = hasher.credential_hash(logout_credential)
    repository.create_session(
        identity,
        uuid4(),
        logout_session_id,
        logout_hash,
        logout_token_digest,
        expires_at,
        f"logout-create-{marker}",
        5,
    )
    repository.logout(logout_session_id, logout_hash, f"logout-{marker}")
    repository.logout(logout_session_id, logout_hash, f"logout-again-{marker}")
    assert repository.get_session(logout_session_id, logout_hash) is None

    with sessions() as db:
        stored_hashes = set(db.scalars(select(CredentialGeneration.secret_hash)))
        assert credential.encode() not in stored_hashes
        events = set(
            db.scalars(select(AuthAudit.event_type).where(AuthAudit.actor_id == created.actor_id))
        )
        assert {
            "session_created",
            "tenant_selected",
            "credential_rotated",
            "credential_replay",
            "session_logged_out",
        } <= events
        actor = db.get(Actor, created.actor_id)
        assert actor is not None


def test_postgres_token_binding_retry_refresh_reuse_and_cap(
    postgres_backend: tuple[Engine, sessionmaker[Session], PostgresSessionRepository],
) -> None:
    _, sessions, repository = postgres_backend
    marker = uuid4().hex
    identity = VerifiedIdentity(
        issuer=f"https://issuer.example/{marker}",
        subject=f"subject-{marker}",
        display_name="Token Binding Test",
    )
    hasher = CredentialHasher(b"b" * 32)
    token_digest = hasher.token_digest(f"provider-token-{marker}")
    client_instance_id = uuid4()
    expires_at = utc_now() + timedelta(hours=1)

    first_session_id = uuid4()
    first_credential = hasher.derive_credential(first_session_id, token_digest)
    first = repository.create_session(
        identity,
        client_instance_id,
        first_session_id,
        hasher.credential_hash(first_credential),
        token_digest,
        expires_at,
        f"first-{marker}",
        1,
    )

    retry_candidate_id = uuid4()
    retry_candidate = hasher.derive_credential(retry_candidate_id, token_digest)
    recovered = repository.create_session(
        identity,
        client_instance_id,
        retry_candidate_id,
        hasher.credential_hash(retry_candidate),
        token_digest,
        expires_at,
        f"retry-{marker}",
        1,
    )
    assert recovered.session_id == first.session_id
    assert hasher.derive_credential(recovered.session_id, token_digest) == first_credential

    with pytest.raises(PermissionError):
        repository.create_session(
            identity,
            uuid4(),
            uuid4(),
            hasher.credential_hash("unreachable-candidate"),
            token_digest,
            expires_at,
            f"foreign-client-{marker}",
            5,
        )

    refresh_result, refresh_view = repository.refresh(
        identity,
        first.session_id,
        hasher.credential_hash(first_credential),
        hasher.credential_hash("unreachable-refresh"),
        token_digest,
        expires_at,
        f"refresh-reuse-{marker}",
    )
    assert (refresh_result, refresh_view) == ("invalid", None)
    assert (
        repository.get_session(first.session_id, hasher.credential_hash(first_credential))
        is not None
    )

    second_digest = hasher.token_digest(f"second-provider-token-{marker}")
    second_session_id = uuid4()
    with pytest.raises(PermissionError):
        repository.create_session(
            identity,
            uuid4(),
            second_session_id,
            hasher.credential_hash(hasher.derive_credential(second_session_id, second_digest)),
            second_digest,
            expires_at,
            f"cap-{marker}",
            1,
        )

    with sessions() as db:
        bindings = db.scalar(
            select(func.count(CredentialGeneration.id)).where(
                CredentialGeneration.provider_token_digest == token_digest
            )
        )
        assert bindings == 1


def test_postgres_concurrent_creation_is_atomic(
    postgres_backend: tuple[Engine, sessionmaker[Session], PostgresSessionRepository],
) -> None:
    _, sessions, repository = postgres_backend
    marker = uuid4().hex
    identity = VerifiedIdentity(
        issuer=f"https://issuer.example/{marker}",
        subject=f"subject-{marker}",
        display_name="Concurrent Test",
    )
    hasher = CredentialHasher(b"c" * 32)
    shared_digest = hasher.token_digest(f"shared-provider-token-{marker}")
    expires_at = utc_now() + timedelta(hours=1)

    def create_with(client_instance_id: UUID) -> str:
        session_id = uuid4()
        credential = hasher.derive_credential(session_id, shared_digest)
        try:
            repository.create_session(
                identity,
                client_instance_id,
                session_id,
                hasher.credential_hash(credential),
                shared_digest,
                expires_at,
                f"concurrent-{marker}",
                5,
            )
            return "created"
        except PermissionError:
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(create_with, (uuid4(), uuid4())))
    assert sorted(outcomes) == ["created", "denied"]

    with sessions() as db:
        lineage_count = db.scalar(
            select(func.count(CredentialGeneration.id)).where(
                CredentialGeneration.provider_token_digest == shared_digest
            )
        )
        assert lineage_count == 1


def test_postgres_active_session_cap_is_atomic_under_concurrency(
    postgres_backend: tuple[Engine, sessionmaker[Session], PostgresSessionRepository],
) -> None:
    _, sessions, repository = postgres_backend
    marker = uuid4().hex
    identity = VerifiedIdentity(
        issuer=f"https://issuer.example/{marker}",
        subject=f"subject-{marker}",
        display_name="Concurrent Cap Test",
    )
    hasher = CredentialHasher(b"d" * 32)
    expires_at = utc_now() + timedelta(hours=1)

    def create_with(index: int) -> str:
        token_digest = hasher.token_digest(f"cap-token-{marker}-{index}")
        session_id = uuid4()
        credential = hasher.derive_credential(session_id, token_digest)
        try:
            repository.create_session(
                identity,
                uuid4(),
                session_id,
                hasher.credential_hash(credential),
                token_digest,
                expires_at,
                f"concurrent-cap-{marker}-{index}",
                1,
            )
            return "created"
        except PermissionError:
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(create_with, (1, 2)))
    assert sorted(outcomes) == ["created", "denied"]

    with sessions() as db:
        actor_id = db.scalar(
            select(Actor.id).where(
                Actor.issuer == identity.issuer,
                Actor.subject == identity.subject,
            )
        )
        assert actor_id is not None
        active_count = db.scalar(
            select(func.count(DeviceSession.id)).where(
                DeviceSession.actor_id == actor_id,
                DeviceSession.revoked_at.is_(None),
                DeviceSession.expires_at > utc_now(),
            )
        )
        assert active_count == 1
