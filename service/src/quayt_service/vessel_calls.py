"""Tenant-scoped vessel-call read model and PostgreSQL queries."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from quayt_service.models import VesselCall

TENANT_CONTEXT_SETTING = "quayt.tenant_id"
VESSEL_CALL_RUNTIME_ROLE = "quayt_vessel_calls_runtime"


def _restrict_vessel_call_role(session: Session) -> None:
    """Enter the migration-owned role and reject unsafe database configuration.

    SET LOCAL restores the connection's role on both commit and rollback. Bootstrap
    superusers are supported for development/CI, but every operational query runs as
    this restricted role. This is not a sandbox for compromised bootstrap credentials:
    an administrator can RESET ROLE or change policies. Deployed service logins must
    not be superusers or owners, nor have membership in privileged roles.
    """
    if not session.in_transaction():
        raise RuntimeError("vessel-call access requires an explicit transaction")
    session.execute(text(f"SET LOCAL ROLE {VESSEL_CALL_RUNTIME_ROLE}"))
    enforced = session.scalar(
        text(
            "SELECT NOT r.rolsuper AND NOT r.rolbypassrls AND NOT r.rolcanlogin "
            "AND NOT r.rolcreaterole AND NOT r.rolcreatedb "
            "AND NOT r.rolreplication AND NOT r.rolinherit "
            "AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m WHERE m.member = r.oid) "
            "AND c.relowner <> r.oid AND c.relrowsecurity AND c.relforcerowsecurity "
            "AND pg_catalog.row_security_active(c.oid) "
            "FROM pg_catalog.pg_roles r CROSS JOIN pg_catalog.pg_class c "
            "WHERE r.rolname = current_user AND c.oid = 'public.vessel_calls'::regclass"
        )
    )
    if enforced is not True:
        raise RuntimeError("vessel-call row security is not enforced")


def set_tenant_context(session: Session, tenant_id: UUID) -> None:
    """Scope vessel-call RLS to one verified tenant for the current transaction.

    Only transaction-local settings are permitted: no tenant survives pool check-in.
    The policy compares canonical UUID text, so missing/empty/malformed values never
    match a row. Callers retain explicit tenant predicates as defense in depth.
    """
    _restrict_vessel_call_role(session)
    session.execute(
        text("SELECT set_config(:setting, :tenant_id, true)"),
        {"setting": TENANT_CONTEXT_SETTING, "tenant_id": str(tenant_id)},
    )


class VesselCallStatus(str, Enum):
    EXPECTED = "expected"
    ARRIVED = "arrived"
    BERTHED = "berthed"
    DEPARTED = "departed"
    CANCELLED = "cancelled"


class VesselCallFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Optional[VesselCallStatus] = None  # noqa: UP045
    query: Optional[str] = Field(default=None, min_length=1, max_length=100)  # noqa: UP045
    eta_from: Optional[datetime] = None  # noqa: UP045
    eta_to: Optional[datetime] = None  # noqa: UP045
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10_000)

    @field_validator("query")
    @classmethod
    def meaningful_query(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("query must contain a non-whitespace character")
        return value

    @field_validator("eta_from", "eta_to")
    @classmethod
    def require_utc_input(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include an offset")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def ordered_range(self) -> VesselCallFilters:
        if self.eta_from and self.eta_to and self.eta_from > self.eta_to:
            raise ValueError("eta_from must not be after eta_to")
        return self


class VesselCallResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    id: UUID
    tenant_id: UUID
    vessel_name: str
    imo_number: str
    agent_name: Optional[str]  # noqa: UP045
    berth: Optional[str]  # noqa: UP045
    status: VesselCallStatus
    eta: datetime
    etd: Optional[datetime]  # noqa: UP045


class VesselCallListResponse(BaseModel):
    items: list[VesselCallResponse]
    total: int
    limit: int
    offset: int


class VesselCallSummaryResponse(BaseModel):
    total: int
    expected: int
    arrived: int
    berthed: int
    departed: int
    cancelled: int


class VesselCallRepository(Protocol):
    def ready(self) -> bool: ...
    def list(self, tenant_id: UUID, filters: VesselCallFilters) -> VesselCallListResponse: ...
    def get(self, tenant_id: UUID, vessel_call_id: UUID) -> VesselCallResponse | None: ...
    def summary(self, tenant_id: UUID) -> VesselCallSummaryResponse: ...


class PostgresVesselCallRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def ready(self) -> bool:
        try:
            with self._sessions.begin() as session:
                _restrict_vessel_call_role(session)
            return True
        except Exception:
            return False

    @staticmethod
    def _predicates(tenant_id: UUID, filters: VesselCallFilters) -> list[ColumnElement[bool]]:
        predicates: list[ColumnElement[bool]] = [VesselCall.tenant_id == tenant_id]
        if filters.status:
            predicates.append(VesselCall.status == filters.status.value)
        if filters.query:
            escaped = filters.query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            predicates.append(
                or_(
                    VesselCall.vessel_name.ilike(pattern, escape="\\"),
                    VesselCall.imo_number.ilike(pattern, escape="\\"),
                )
            )
        if filters.eta_from:
            predicates.append(VesselCall.eta >= filters.eta_from)
        if filters.eta_to:
            predicates.append(VesselCall.eta <= filters.eta_to)
        return predicates

    def list(self, tenant_id: UUID, filters: VesselCallFilters) -> VesselCallListResponse:
        predicates = self._predicates(tenant_id, filters)
        with self._sessions.begin() as session:
            set_tenant_context(session, tenant_id)
            total = session.scalar(select(func.count()).select_from(VesselCall).where(*predicates))
            rows = session.scalars(
                select(VesselCall)
                .where(*predicates)
                .order_by(VesselCall.eta, VesselCall.id)
                .limit(filters.limit)
                .offset(filters.offset)
            ).all()
        return VesselCallListResponse(
            items=[VesselCallResponse.model_validate(row) for row in rows],
            total=int(total or 0),
            limit=filters.limit,
            offset=filters.offset,
        )

    def get(self, tenant_id: UUID, vessel_call_id: UUID) -> VesselCallResponse | None:
        with self._sessions.begin() as session:
            set_tenant_context(session, tenant_id)
            row = session.scalar(
                select(VesselCall).where(
                    VesselCall.tenant_id == tenant_id, VesselCall.id == vessel_call_id
                )
            )
        return None if row is None else VesselCallResponse.model_validate(row)

    def summary(self, tenant_id: UUID) -> VesselCallSummaryResponse:
        with self._sessions.begin() as session:
            set_tenant_context(session, tenant_id)
            rows = session.execute(
                select(VesselCall.status, func.count())
                .where(VesselCall.tenant_id == tenant_id)
                .group_by(VesselCall.status)
            ).all()
        counts = {status: count for status, count in rows}
        return VesselCallSummaryResponse(
            total=sum(counts.values()),
            **{status.value: counts.get(status.value, 0) for status in VesselCallStatus},
        )
