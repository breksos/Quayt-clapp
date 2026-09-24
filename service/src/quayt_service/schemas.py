"""Versioned authentication API DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from quayt_service.domain import SessionView


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuthConfigResponse(ApiModel):
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    client_id: str
    scopes: tuple[str, ...]
    pkce_methods: tuple[str, ...] = ("S256",)
    loopback_host: str = "127.0.0.1"


class CreateSessionRequest(ApiModel):
    client_instance_id: UUID


class SelectTenantRequest(ApiModel):
    tenant_id: UUID
    expected_session_version: int = Field(ge=1)


class ActorResponse(ApiModel):
    id: UUID
    display_name: Optional[str] = None  # noqa: UP045


class MembershipResponse(ApiModel):
    tenant_id: UUID
    tenant_name: str
    roles: tuple[str, ...]


class SessionResponse(ApiModel):
    session_id: UUID
    version: int
    actor: ActorResponse
    memberships: tuple[MembershipResponse, ...]
    selected_tenant_id: Optional[UUID] = None  # noqa: UP045
    expires_at: datetime

    @classmethod
    def from_view(cls, view: SessionView) -> SessionResponse:
        return cls(
            session_id=view.session_id,
            version=view.version,
            actor=ActorResponse(id=view.actor_id, display_name=view.display_name),
            memberships=tuple(
                MembershipResponse(
                    tenant_id=item.tenant_id,
                    tenant_name=item.tenant_name,
                    roles=item.roles,
                )
                for item in view.memberships
            ),
            selected_tenant_id=view.selected_tenant_id,
            expires_at=view.expires_at,
        )


class IssuedSessionResponse(ApiModel):
    session: SessionResponse
    session_credential: str
