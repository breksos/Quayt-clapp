"""Phase 2 versioned authentication and session routes."""

from __future__ import annotations

from typing import Annotated, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, Request, Response

from quayt_service.auth import (
    _session_credential,
    authentication_failed,
    bearer_token,
    require_session,
    tenant_access_denied,
)
from quayt_service.domain import SessionView
from quayt_service.errors import ServiceError
from quayt_service.oidc import OidcVerifier, TokenVerificationError
from quayt_service.repository import SessionRepository, session_expiry
from quayt_service.schemas import (
    AuthConfigResponse,
    CreateSessionRequest,
    IssuedSessionResponse,
    SelectTenantRequest,
    SessionResponse,
)
from quayt_service.security import CredentialHasher
from quayt_service.settings import Settings

router = APIRouter(prefix="/api/v1", tags=["authentication"])


def _request_id(request: Request) -> str:
    return str(request.state.request_id)


def _components(
    request: Request,
) -> tuple[Settings, SessionRepository, OidcVerifier, CredentialHasher]:
    return (
        request.app.state.settings,
        request.app.state.session_repository,
        request.app.state.oidc_verifier,
        request.app.state.credential_hasher,
    )


@router.get("/auth/config", response_model=AuthConfigResponse)
def auth_config(request: Request) -> AuthConfigResponse:
    settings: Settings = request.app.state.settings
    assert settings.auth_issuer is not None
    return AuthConfigResponse(
        issuer=settings.auth_issuer,
        authorization_endpoint=settings.authorization_endpoint,
        token_endpoint=settings.token_endpoint,
        client_id=settings.oidc_client_id,
        scopes=settings.oidc_scopes,
    )


@router.post("/session", response_model=IssuedSessionResponse, status_code=201)
def create_session(
    body: CreateSessionRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),  # noqa: UP045, B008
) -> IssuedSessionResponse:
    settings, repository, verifier, hasher = _components(request)
    token = bearer_token(authorization)
    try:
        identity = verifier.verify(token)
    except TokenVerificationError as exc:
        raise authentication_failed() from exc
    session_id = uuid4()
    token_digest = hasher.token_digest(token)
    credential = hasher.derive_credential(session_id, token_digest)
    try:
        view = repository.create_session(
            identity=identity,
            client_instance_id=body.client_instance_id,
            session_id=session_id,
            credential_hash=hasher.credential_hash(credential),
            token_digest=token_digest,
            expires_at=session_expiry(settings.session_ttl_seconds),
            request_id=_request_id(request),
            max_active_sessions=settings.max_active_sessions_per_actor,
        )
    except PermissionError as exc:
        raise authentication_failed() from exc
    recovered_credential = hasher.derive_credential(view.session_id, token_digest)
    return IssuedSessionResponse(
        session=SessionResponse.from_view(view),
        session_credential=recovered_credential,
    )


@router.get("/session", response_model=SessionResponse)
def get_session(view: Annotated[SessionView, Depends(require_session)]) -> SessionResponse:
    return SessionResponse.from_view(view)


@router.post("/session/tenant", response_model=SessionResponse)
def select_tenant(
    body: SelectTenantRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),  # noqa: UP045, B008
) -> SessionResponse:
    credential = _session_credential(authorization)
    _, repository, _, hasher = _components(request)
    session_id = hasher.parse_session_id(credential)
    if session_id is None:
        raise authentication_failed()
    result, view = repository.select_tenant(
        session_id,
        hasher.credential_hash(credential),
        body.tenant_id,
        body.expected_session_version,
        _request_id(request),
    )
    if result == "conflict":
        raise ServiceError(status_code=409, code="session_conflict", message="Session changed")
    if result == "denied":
        raise tenant_access_denied()
    if result != "ok" or view is None:
        raise authentication_failed()
    return SessionResponse.from_view(view)


@router.post("/session/refresh", response_model=IssuedSessionResponse)
def refresh_session(
    request: Request,
    authorization: Optional[str] = Header(default=None),  # noqa: UP045, B008
    quayt_session_credential: Optional[str] = Header(  # noqa: UP045, B008
        default=None, alias="X-Quayt-Session-Credential"
    ),
) -> IssuedSessionResponse:
    settings, repository, verifier, hasher = _components(request)
    token = bearer_token(authorization)
    if not quayt_session_credential:
        raise authentication_failed()
    session_id = hasher.parse_session_id(quayt_session_credential)
    if session_id is None:
        raise authentication_failed()
    try:
        identity = verifier.verify(token)
    except TokenVerificationError as exc:
        raise authentication_failed() from exc
    token_digest = hasher.token_digest(token)
    new_credential = hasher.derive_credential(session_id, token_digest)
    result, view = repository.refresh(
        identity,
        session_id,
        hasher.credential_hash(quayt_session_credential),
        hasher.credential_hash(new_credential),
        token_digest,
        session_expiry(settings.session_ttl_seconds),
        _request_id(request),
    )
    if result != "ok" or view is None:
        raise authentication_failed()
    return IssuedSessionResponse(
        session=SessionResponse.from_view(view),
        session_credential=new_credential,
    )


@router.post("/session/logout", status_code=204)
def logout_session(
    request: Request,
    authorization: Optional[str] = Header(default=None),  # noqa: UP045, B008
) -> Response:
    credential = _session_credential(authorization)
    _, repository, _, hasher = _components(request)
    session_id = hasher.parse_session_id(credential)
    if session_id is not None:
        repository.logout(
            session_id,
            hasher.credential_hash(credential),
            _request_id(request),
        )
    return Response(status_code=204)
