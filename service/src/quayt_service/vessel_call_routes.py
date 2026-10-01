"""Authenticated, tenant-scoped vessel-call HTTP routes."""

from datetime import datetime
from typing import Annotated, Optional, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import ValidationError

from quayt_service.auth import require_request_context
from quayt_service.context import RequestContext
from quayt_service.errors import ServiceError
from quayt_service.vessel_calls import (
    VesselCallFilters,
    VesselCallListResponse,
    VesselCallRepository,
    VesselCallResponse,
    VesselCallStatus,
    VesselCallSummaryResponse,
)

router = APIRouter(prefix="/api/v1/vessel-calls", tags=["vessel-calls"])


def _repository(request: Request) -> VesselCallRepository:
    return cast(VesselCallRepository, request.app.state.vessel_call_repository)


def _filters(
    status: Optional[VesselCallStatus] = Query(default=None),  # noqa: UP045, B008
    query: Optional[str] = Query(default=None, min_length=1, max_length=100),  # noqa: UP045, B008
    eta_from: Optional[datetime] = Query(default=None),  # noqa: UP045, B008
    eta_to: Optional[datetime] = Query(default=None),  # noqa: UP045, B008
    limit: int = Query(default=50, ge=1, le=100),  # noqa: B008
    offset: int = Query(default=0, ge=0, le=10_000),  # noqa: B008
) -> VesselCallFilters:
    try:
        return VesselCallFilters(
            status=status,
            query=query,
            eta_from=eta_from,
            eta_to=eta_to,
            limit=limit,
            offset=offset,
        )
    except ValidationError as exc:
        raise ServiceError(
            status_code=422,
            code="validation_failed",
            message="Request validation failed",
        ) from exc


@router.get("", response_model=VesselCallListResponse)
def list_vessel_calls(
    filters: Annotated[VesselCallFilters, Depends(_filters)],
    context: Annotated[RequestContext, Depends(require_request_context)],
    repository: Annotated[VesselCallRepository, Depends(_repository)],
) -> VesselCallListResponse:
    return repository.list(context.tenant.tenant_id, filters)


@router.get("/summary", response_model=VesselCallSummaryResponse)
def vessel_call_summary(
    context: Annotated[RequestContext, Depends(require_request_context)],
    repository: Annotated[VesselCallRepository, Depends(_repository)],
) -> VesselCallSummaryResponse:
    return repository.summary(context.tenant.tenant_id)


@router.get("/{vessel_call_id}", response_model=VesselCallResponse)
def get_vessel_call(
    vessel_call_id: UUID,
    context: Annotated[RequestContext, Depends(require_request_context)],
    repository: Annotated[VesselCallRepository, Depends(_repository)],
) -> VesselCallResponse:
    item = repository.get(context.tenant.tenant_id, vessel_call_id)
    if item is None:
        raise ServiceError(status_code=404, code="not_found", message="Resource not found")
    return item
