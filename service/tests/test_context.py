from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from quayt_service.context import ActorContext, RequestContext, TenantContext


def test_request_context_requires_tenant_and_actor() -> None:
    with pytest.raises(ValidationError):
        RequestContext.model_validate({})

    with pytest.raises(ValidationError):
        RequestContext.model_validate(
            {"actor": {"actor_id": str(uuid4()), "subject": "user:42", "scopes": []}}
        )


def test_tenant_context_rejects_nil_and_has_no_default() -> None:
    with pytest.raises(ValidationError):
        TenantContext.model_validate({})
    with pytest.raises(ValidationError, match="tenant_id cannot be nil"):
        TenantContext(tenant_id=UUID(int=0))


def test_actor_context_requires_explicit_non_nil_identity() -> None:
    with pytest.raises(ValidationError):
        ActorContext.model_validate({"subject": "anonymous", "scopes": ["admin"]})
    with pytest.raises(ValidationError, match="actor_id cannot be nil"):
        ActorContext(actor_id=UUID(int=0), subject="system", scopes=("admin",))


def test_valid_context_is_immutable_and_explicit() -> None:
    tenant_id = uuid4()
    actor_id = uuid4()
    context = RequestContext(
        tenant=TenantContext(tenant_id=tenant_id),
        actor=ActorContext(actor_id=actor_id, subject="user:42", scopes=("operations:read",)),
    )

    assert context.tenant.tenant_id == tenant_id
    assert context.actor.actor_id == actor_id
    with pytest.raises(ValidationError):
        context.tenant.tenant_id = uuid4()
