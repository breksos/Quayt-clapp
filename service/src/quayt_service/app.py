"""ASGI application factory for the Phase 1 central service."""

from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from quayt_service.auth_routes import router as auth_router
from quayt_service.database import create_database_engine, create_session_factory
from quayt_service.errors import ErrorBody, ErrorDetail, ErrorEnvelope, ServiceError
from quayt_service.oidc import JwtOidcVerifier, OidcVerifier
from quayt_service.repository import PostgresSessionRepository, SessionRepository
from quayt_service.security import CredentialHasher
from quayt_service.settings import Settings
from quayt_service.vessel_call_routes import router as vessel_call_router
from quayt_service.vessel_calls import PostgresVesselCallRepository, VesselCallRepository


def _request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    return str(request_id if request_id is not None else uuid4())


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: list[ErrorDetail] | None = None,
) -> JSONResponse:
    envelope = ErrorEnvelope(
        error=ErrorBody(
            code=code,
            message=message,
            request_id=_request_id(request),
            details=details,
        )
    )
    return JSONResponse(status_code=status_code, content=envelope.model_dump(exclude_none=True))


def create_app(
    settings: Settings | None = None,
    *,
    repository: SessionRepository | None = None,
    oidc_verifier: OidcVerifier | None = None,
    vessel_call_repository: VesselCallRepository | None = None,
) -> FastAPI:
    active_settings = Settings.from_env() if settings is None else settings
    active_settings.validate()

    app = FastAPI(
        title="Quayt central service",
        version=active_settings.service_version,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/v1/openapi.json",
    )
    app.state.settings = active_settings
    session_factory = None
    if repository is None or vessel_call_repository is None:
        assert active_settings.database_url is not None
        engine = create_database_engine(active_settings.database_url)
        app.state.database_engine = engine
        session_factory = create_session_factory(engine)
    if repository is None:
        assert session_factory is not None
        repository = PostgresSessionRepository(session_factory)
    if vessel_call_repository is None:
        assert session_factory is not None
        vessel_call_repository = PostgresVesselCallRepository(session_factory)
    app.state.session_repository = repository
    app.state.vessel_call_repository = vessel_call_repository
    app.state.oidc_verifier = oidc_verifier or JwtOidcVerifier(active_settings)
    app.state.credential_hasher = CredentialHasher(active_settings.session_hash_key)

    if active_settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(active_settings.allowed_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "If-Match",
                "Idempotency-Key",
                "X-Quayt-Session-Credential",
            ],
        )

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = str(uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        if request.url.path.startswith(("/api/v1/session", "/api/v1/vessel-calls")):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        return response

    @app.exception_handler(ServiceError)
    async def handle_service_error(request: Request, exc: ServiceError) -> JSONResponse:
        details = [ErrorDetail.model_validate(item) for item in exc.details or []] or None
        return _error_response(
            request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=details,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = [
            ErrorDetail(
                field=".".join(str(part) for part in error["loc"]),
                reason=error["type"],
            )
            for error in exc.errors()
        ]
        return _error_response(
            request,
            status_code=422,
            code="validation_failed",
            message="Request validation failed",
            details=details,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return _error_response(
                request, status_code=404, code="not_found", message="Resource not found"
            )
        return _error_response(
            request,
            status_code=exc.status_code,
            code="http_error",
            message="HTTP request failed",
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        del exc
        return _error_response(
            request,
            status_code=500,
            code="internal_error",
            message="An unexpected error occurred",
        )

    @app.get("/health/live", include_in_schema=False)
    async def liveness() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready", include_in_schema=False)
    def readiness() -> JSONResponse:
        ready = (
            app.state.session_repository.ready()
            and app.state.vessel_call_repository.ready()
            and app.state.oidc_verifier.ready()
        )
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"status": "ready" if ready else "not_ready"},
        )

    @app.get("/api/v1", tags=["service"])
    async def api_boundary() -> dict[str, str]:
        return {
            "api_version": "v1",
            "service": active_settings.service_name,
            "service_version": active_settings.service_version,
        }

    app.include_router(auth_router)
    app.include_router(vessel_call_router)

    return app
