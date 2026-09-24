"""Authentication domain values shared by HTTP and persistence boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class VerifiedIdentity:
    issuer: str
    subject: str
    display_name: str | None


@dataclass(frozen=True)
class MembershipView:
    tenant_id: UUID
    tenant_name: str
    roles: tuple[str, ...]


@dataclass(frozen=True)
class SessionView:
    session_id: UUID
    version: int
    actor_id: UUID
    subject: str
    display_name: str | None
    memberships: tuple[MembershipView, ...]
    selected_tenant_id: UUID | None
    expires_at: datetime


@dataclass(frozen=True)
class IssuedSession:
    view: SessionView
    credential: str
