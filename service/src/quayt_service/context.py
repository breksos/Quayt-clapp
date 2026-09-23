"""Explicit authenticated request context types.

These types do not parse client headers or tokens. A later authentication boundary must
construct them from verified server-issued identity and membership records.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

NonBlank = Annotated[str, Field(min_length=1, max_length=255, pattern=r".*\S.*")]


class TenantContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: UUID

    @field_validator("tenant_id")
    @classmethod
    def reject_nil_tenant(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("tenant_id cannot be nil")
        return value


class ActorContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    actor_id: UUID
    subject: NonBlank
    scopes: tuple[NonBlank, ...]

    @field_validator("actor_id")
    @classmethod
    def reject_nil_actor(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("actor_id cannot be nil")
        return value


class RequestContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant: TenantContext
    actor: ActorContext
