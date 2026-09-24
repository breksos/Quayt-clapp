"""Phase 2 authoritative PostgreSQL model."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


JSON_VALUE = JSON().with_variant(postgresql.JSONB(), "postgresql")


class Actor(Base):
    __tablename__ = "actors"
    __table_args__ = (
        CheckConstraint("status IN ('active','disabled')", name="ck_actors_status"),
        UniqueConstraint("issuer", "subject", name="uq_actors_issuer_subject"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(255))  # noqa: UP045
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (CheckConstraint("status IN ('active','disabled')", name="ck_tenants_status"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        CheckConstraint("status IN ('active','disabled')", name="ck_memberships_status"),
        CheckConstraint("version > 0", name="ck_memberships_version"),
        UniqueConstraint("actor_id", "tenant_id", name="uq_memberships_actor_tenant"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("actors.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    roles: Mapped[list[str]] = mapped_column(JSON_VALUE, nullable=False, default=list)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DeviceSession(Base):
    __tablename__ = "device_sessions"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_device_sessions_version"),
        Index("ix_device_sessions_actor_client", "actor_id", "client_instance_id"),
        Index(
            "uq_device_sessions_active_actor_client",
            "actor_id",
            "client_instance_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("actors.id", ondelete="CASCADE"), nullable=False
    )
    client_instance_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    selected_tenant_id: Mapped[Optional[UUID]] = mapped_column(  # noqa: UP045
        ForeignKey("tenants.id", ondelete="SET NULL")
    )
    oidc_token_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))  # noqa: UP045
    revoke_reason: Mapped[Optional[str]] = mapped_column(String(64))  # noqa: UP045


class CredentialGeneration(Base):
    __tablename__ = "credential_generations"
    __table_args__ = (
        CheckConstraint("status IN ('current','rotated','revoked')", name="ck_credential_status"),
        UniqueConstraint("session_id", "generation", name="uq_credential_session_generation"),
        UniqueConstraint("secret_hash", name="uq_credential_secret_hash"),
        UniqueConstraint("provider_token_digest", name="uq_credential_provider_token_digest"),
        Index(
            "uq_credential_current",
            "session_id",
            unique=True,
            postgresql_where=text("status = 'current'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("device_sessions.id", ondelete="CASCADE"), nullable=False
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    secret_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    provider_token_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))  # noqa: UP045


class AuthAudit(Base):
    __tablename__ = "auth_audit"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    actor_id: Mapped[Optional[UUID]] = mapped_column(Uuid)  # noqa: UP045
    session_id: Mapped[Optional[UUID]] = mapped_column(Uuid)  # noqa: UP045
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False, default=dict)
