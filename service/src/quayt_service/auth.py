"""Fail-closed HTTP authentication and tenant-context construction."""

from __future__ import annotations

from typing import Optional

from fastapi import Header, Request

from quayt_service.context import ActorContext, RequestContext, TenantContext
from quayt_service.domain import SessionView
from quayt_service.errors import ServiceError
from quayt_service.repository import SessionRepository
from quayt_service.security import CredentialHasher


def authentication_failed() -> ServiceError:
    return ServiceError(
        status_code=401,
        code="authentication_failed",
        message="Authentication failed",
    )


def tenant_access_denied() -> ServiceError:
    return ServiceError(
        status_code=403,
        code="tenant_access_denied",
        message="Tenant access denied",
    )


def _session_credential(authorization: Optional[str]) -> str:  # noqa: UP045
    if authorization is None:
        raise authentication_failed()
    scheme, separator, credential = authorization.partition(" ")
    if separator != " " or scheme.lower() != "session" or not credential:
        raise authentication_failed()
    return credential


def bearer_token(authorization: Optional[str]) -> str:  # noqa: UP045
    if authorization is None:
        raise authentication_failed()
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token:
        raise authentication_failed()
    return token


def require_session(
    request: Request,
    authorization: Optional[str] = Header(default=None),  # noqa: UP045, B008
) -> SessionView:
    credential = _session_credential(authorization)
    hasher: CredentialHasher = request.app.state.credential_hasher
    repository: SessionRepository = request.app.state.session_repository
    session_id = hasher.parse_session_id(credential)
    if session_id is None:
        raise authentication_failed()
    view = repository.get_session(session_id, hasher.credential_hash(credential))
    if view is None:
        raise authentication_failed()
    return view


def require_request_context(
    request: Request,
    authorization: Optional[str] = Header(default=None),  # noqa: UP045, B008
) -> RequestContext:
    view = require_session(request, authorization)
    if view.selected_tenant_id is None:
        raise tenant_access_denied()
    membership = next(
        (item for item in view.memberships if item.tenant_id == view.selected_tenant_id), None
    )
    if membership is None:
        raise tenant_access_denied()
    return RequestContext(
        tenant=TenantContext(tenant_id=membership.tenant_id),
        actor=ActorContext(
            actor_id=view.actor_id,
            subject=view.subject,
            scopes=membership.roles,
        ),
    )
