from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine, delete, event, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from quayt_service.database import create_session_factory
from quayt_service.models import Actor, Membership, Tenant, VesselCall
from quayt_service.provision_demo import provision_demo
from quayt_service.seed_vessel_calls import SEED_ROWS, seed_vessel_calls
from quayt_service.settings import Environment, Settings
from quayt_service.vessel_calls import (
    VESSEL_CALL_RUNTIME_ROLE,
    PostgresVesselCallRepository,
    VesselCallFilters,
    VesselCallStatus,
    set_tenant_context,
)


@dataclass
class VesselDatabase:
    engine: Engine
    sessions: sessionmaker[Session]
    own: UUID
    foreign: UUID
    own_ids: list[UUID]
    foreign_id: UUID
    now: datetime

    @property
    def repository(self) -> PostgresVesselCallRepository:
        return PostgresVesselCallRepository(self.sessions)


@pytest.fixture
def vessel_database() -> Iterator[VesselDatabase]:
    database_url = os.environ.get("QUAYT_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("QUAYT_DATABASE_URL is not set")
    # One physical connection makes pool reuse deterministic, not incidental.
    engine = create_engine(database_url, pool_pre_ping=True, pool_size=1, max_overflow=0)
    sessions = create_session_factory(engine)
    own, foreign = uuid4(), uuid4()
    own_ids = sorted([uuid4(), uuid4(), uuid4()])
    foreign_id = uuid4()
    now = datetime(2030, 1, 5, tzinfo=timezone.utc)
    try:
        with sessions.begin() as session:
            session.add_all(
                Tenant(
                    id=tenant, name="RLS fixture", status="active", created_at=now, updated_at=now
                )
                for tenant in (own, foreign)
            )
        for tenant, identifier, name, imo, status, eta in (
            (own, own_ids[0], "Northstar", "9000011", "expected", now),
            (own, own_ids[1], "Northstar 100%_", "9000012", "arrived", now),
            (own, own_ids[2], "Meridian", "9000013", "berthed", now + timedelta(days=1)),
            (foreign, foreign_id, "Northstar", "9000011", "expected", now),
        ):
            with sessions.begin() as session:
                set_tenant_context(session, tenant)
                session.add(
                    VesselCall(
                        id=identifier,
                        tenant_id=tenant,
                        vessel_name=name,
                        imo_number=imo,
                        agent_name=None,
                        berth=None,
                        status=status,
                        eta=eta,
                        etd=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
        yield VesselDatabase(engine, sessions, own, foreign, own_ids, foreign_id, now)
    finally:
        try:
            with sessions.begin() as session:
                session.execute(delete(Tenant).where(Tenant.id.in_([own, foreign])))
        finally:
            engine.dispose()


def test_postgres_tenant_isolation_filters_and_deterministic_seed(
    vessel_database: VesselDatabase,
    settings: Settings,
) -> None:
    db = vessel_database
    repository = db.repository
    assert repository.ready()
    assert [row.id for row in repository.list(db.own, VesselCallFilters()).items] == db.own_ids
    assert [row.id for row in repository.list(db.foreign, VesselCallFilters()).items] == [
        db.foreign_id
    ]
    assert repository.get(db.own, db.own_ids[0]) is not None
    assert repository.get(db.own, db.foreign_id) is None
    assert repository.get(db.foreign, db.own_ids[0]) is None
    assert repository.summary(db.own).model_dump() == {
        "total": 3,
        "expected": 1,
        "arrived": 1,
        "berthed": 1,
        "departed": 0,
        "cancelled": 0,
    }
    assert repository.summary(db.foreign).total == 1
    for filters, expected in (
        (VesselCallFilters(status=VesselCallStatus.EXPECTED), db.own_ids[:1]),
        (VesselCallFilters(query="NORTH"), db.own_ids[:2]),
        (VesselCallFilters(query="9000011"), db.own_ids[:1]),
        (VesselCallFilters(query="%_"), db.own_ids[1:2]),
        (VesselCallFilters(eta_from=db.now, eta_to=db.now), db.own_ids[:2]),
        (VesselCallFilters(eta_from=db.now + timedelta(days=1)), db.own_ids[2:]),
        (
            VesselCallFilters(
                status=VesselCallStatus.EXPECTED, query="north", eta_from=db.now, eta_to=db.now
            ),
            db.own_ids[:1],
        ),
        (VesselCallFilters(status=VesselCallStatus.CANCELLED), []),
    ):
        result = repository.list(db.own, filters)
        assert [row.id for row in result.items] == expected
        assert result.total == len(expected)
    page = repository.list(db.own, VesselCallFilters(limit=1, offset=1))
    assert [row.id for row in page.items] == db.own_ids[1:2]
    assert (page.total, page.limit, page.offset) == (3, 1, 1)

    development = replace(settings, environment=Environment.DEVELOPMENT)
    assert seed_vessel_calls(development, db.own, db.sessions) == len(SEED_ROWS)
    first = repository.list(db.own, VesselCallFilters(limit=100))
    assert seed_vessel_calls(development, db.own, db.sessions) == len(SEED_ROWS)
    assert repository.list(db.own, VesselCallFilters(limit=100)) == first
    assert first.total == 3 + len(SEED_ROWS)
    assert repository.summary(db.foreign).total == 1


def test_postgres_demo_provisioning_is_oidc_compatible_and_idempotent(
    vessel_database: VesselDatabase,
    settings: Settings,
) -> None:
    db = vessel_database
    development = replace(settings, environment=Environment.DEVELOPMENT)
    subject = f"demo-{uuid4().hex}"
    first = provision_demo(development, subject, db.sessions)
    try:
        second = provision_demo(development, subject, db.sessions)
        assert second == first
        assert first.vessel_call_count == len(SEED_ROWS)

        with db.sessions() as session:
            actor = session.scalar(
                select(Actor).where(
                    Actor.issuer == development.auth_issuer,
                    Actor.subject == subject,
                )
            )
            assert actor is not None
            assert actor.id == first.actor_id
            assert actor.status == "active"
            membership = session.scalar(
                select(Membership).where(
                    Membership.actor_id == first.actor_id,
                    Membership.tenant_id == first.tenant_id,
                )
            )
            assert membership is not None
            assert membership.id == first.membership_id
            assert membership.status == "active"
            assert membership.roles == ["operator"]
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(Membership)
                    .where(
                        Membership.actor_id == first.actor_id,
                        Membership.tenant_id == first.tenant_id,
                    )
                )
                == 1
            )
            assert session.scalar(
                select(func.count())
                .select_from(VesselCall)
                .where(VesselCall.tenant_id == first.tenant_id)
            ) == len(SEED_ROWS)
    finally:
        with db.sessions.begin() as session:
            session.execute(delete(Tenant).where(Tenant.id == first.tenant_id))
            session.execute(delete(Actor).where(Actor.id == first.actor_id))


def test_repository_and_seed_execute_as_restricted_role(
    vessel_database: VesselDatabase,
    settings: Settings,
) -> None:
    db = vessel_database
    observed: list[tuple[str, str]] = []

    def inspect_access(connection, cursor, statement, parameters, context, executemany):
        del cursor, parameters, context, executemany
        if "FROM vessel_calls" in statement or "INSERT INTO vessel_calls" in statement:
            # Separate DBAPI cursor avoids recursively invoking this SQLAlchemy event.
            with connection.connection.cursor() as probe:
                probe.execute("SELECT current_user, current_setting('quayt.tenant_id', true)")
                observed.append(probe.fetchone())

    event.listen(db.engine, "before_cursor_execute", inspect_access)
    try:
        operations = (
            (lambda: db.repository.list(db.own, VesselCallFilters()), db.own, 2),
            (lambda: db.repository.get(db.foreign, db.foreign_id), db.foreign, 1),
            (lambda: db.repository.summary(db.own), db.own, 1),
            (
                lambda: seed_vessel_calls(
                    replace(settings, environment=Environment.DEVELOPMENT), db.foreign, db.sessions
                ),
                db.foreign,
                len(SEED_ROWS),
            ),
        )
        for operation, tenant, count in operations:
            observed.clear()
            operation()
            assert observed == [(VESSEL_CALL_RUNTIME_ROLE, str(tenant))] * count
    finally:
        event.remove(db.engine, "before_cursor_execute", inspect_access)


@pytest.mark.parametrize("context_value", [None, "", "not-a-uuid", "foreign"])
def test_postgres_rls_fails_closed_for_missing_malformed_and_foreign_context(
    vessel_database: VesselDatabase,
    context_value: str | None,
) -> None:
    db = vessel_database
    value = str(db.foreign) if context_value == "foreign" else context_value
    if context_value is None:
        # A fresh backend proves the never-set case, not just SET LOCAL's empty reset.
        db.engine.dispose()
    with db.sessions.begin() as session:
        session.execute(text(f"SET LOCAL ROLE {VESSEL_CALL_RUNTIME_ROLE}"))
        if context_value is None:
            assert session.scalar(text("SELECT current_setting('quayt.tenant_id', true)")) is None
        if value is not None:
            session.execute(
                text("SELECT set_config('quayt.tenant_id', :value, true)"), {"value": value}
            )
        visible = session.scalars(select(VesselCall.id)).all()
        assert visible == ([db.foreign_id] if context_value == "foreign" else [])
        assert session.scalar(select(VesselCall.id).where(VesselCall.id == db.own_ids[0])) is None
        changed = session.execute(
            update(VesselCall).where(VesselCall.id.in_(db.own_ids)).values(vessel_name="forbidden")
        )
        assert changed.rowcount == 0

    with pytest.raises(DBAPIError) as denied, db.sessions.begin() as session:
        session.execute(text(f"SET LOCAL ROLE {VESSEL_CALL_RUNTIME_ROLE}"))
        if value is not None:
            session.execute(
                text("SELECT set_config('quayt.tenant_id', :value, true)"), {"value": value}
            )
        session.execute(
            insert(VesselCall).values(
                id=uuid4(),
                tenant_id=db.own,
                vessel_name="forbidden",
                imo_number="9000099",
                status="expected",
                eta=db.now,
                created_at=db.now,
                updated_at=db.now,
            )
        )
    assert denied.value.orig.sqlstate == "42501"  # insufficient_privilege, not another SQL error


def test_rls_rejects_moving_a_visible_row_to_another_tenant(
    vessel_database: VesselDatabase,
) -> None:
    db = vessel_database
    with pytest.raises(DBAPIError) as denied, db.sessions.begin() as session:
        set_tenant_context(session, db.own)
        session.execute(
            update(VesselCall).where(VesselCall.id == db.own_ids[0]).values(tenant_id=db.foreign)
        )
    assert denied.value.orig.sqlstate == "42501"
    assert db.repository.get(db.own, db.own_ids[0]) is not None


@pytest.mark.parametrize("rollback", [False, True])
def test_pool_reuse_clears_role_and_tenant_after_commit_and_rollback(
    vessel_database: VesselDatabase,
    rollback: bool,
) -> None:
    db = vessel_database
    with db.sessions() as session:
        transaction = session.begin()
        pid, login = session.execute(text("SELECT pg_backend_pid(), current_user")).one()
        set_tenant_context(session, db.own)
        assert session.scalar(select(func.count()).select_from(VesselCall)) == 3
        if rollback:
            transaction.rollback()
        else:
            transaction.commit()
    with db.sessions.begin() as session:
        assert session.execute(text("SELECT pg_backend_pid(), current_user")).one() == (pid, login)
        assert session.scalar(text("SELECT current_setting('quayt.tenant_id', true)")) in (None, "")
        session.execute(text(f"SET LOCAL ROLE {VESSEL_CALL_RUNTIME_ROLE}"))
        assert session.scalar(select(func.count()).select_from(VesselCall)) == 0
    assert db.repository.summary(db.foreign).total == 1
    assert db.repository.summary(db.own).total == 3
    with db.sessions.begin() as session:
        assert session.scalar(text("SELECT pg_backend_pid()")) == pid
        session.execute(text(f"SET LOCAL ROLE {VESSEL_CALL_RUNTIME_ROLE}"))
        assert session.scalars(select(VesselCall.id)).all() == []


@pytest.mark.parametrize(
    "command",
    [
        "ALTER TABLE vessel_calls DISABLE ROW LEVEL SECURITY",
        "ALTER TABLE vessel_calls NO FORCE ROW LEVEL SECURITY",
        "DROP POLICY vessel_calls_tenant_isolation ON vessel_calls",
        f"ALTER ROLE {VESSEL_CALL_RUNTIME_ROLE} BYPASSRLS",
        "TRUNCATE vessel_calls",
        "DELETE FROM vessel_calls",
    ],
)
def test_runtime_role_cannot_disable_or_bypass_policy(
    vessel_database: VesselDatabase,
    command: str,
) -> None:
    db = vessel_database
    with pytest.raises(DBAPIError) as denied, db.sessions.begin() as session:
        set_tenant_context(session, db.own)
        session.execute(text(command))
    assert denied.value.orig.sqlstate == "42501"
    assert db.repository.summary(db.own).total == 3


def test_row_security_off_cannot_bypass_runtime_policy(vessel_database: VesselDatabase) -> None:
    db = vessel_database
    with pytest.raises(DBAPIError) as denied, db.sessions.begin() as session:
        set_tenant_context(session, db.own)
        session.execute(text("SET LOCAL row_security = off"))
        session.scalars(select(VesselCall.id)).all()
    assert denied.value.orig.sqlstate == "42501"


def test_migrated_rls_and_runtime_role_attributes(vessel_database: VesselDatabase) -> None:
    with vessel_database.sessions.begin() as session:
        attributes = session.execute(
            text(
                "SELECT r.rolcanlogin, r.rolsuper, r.rolbypassrls, r.rolcreatedb, r.rolcreaterole, "
                "r.rolinherit, r.rolreplication, c.relowner = r.oid, c.relrowsecurity, "
                "c.relforcerowsecurity FROM pg_roles r CROSS JOIN pg_class c "
                "WHERE r.rolname = :role AND c.oid = 'public.vessel_calls'::regclass"
            ),
            {"role": VESSEL_CALL_RUNTIME_ROLE},
        ).one()
        assert attributes == (False,) * 8 + (True, True)
        assert (
            session.scalar(
                text(
                    "SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member "
                    "WHERE r.rolname = :role"
                ),
                {"role": VESSEL_CALL_RUNTIME_ROLE},
            )
            == 0
        )
