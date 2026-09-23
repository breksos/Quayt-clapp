"""Stable public error contract for all HTTP failures."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: Optional[str] = None  # noqa: UP045 -- Pydantic evaluates this under Python 3.9.
    reason: str


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    message: str
    request_id: str
    details: Optional[list[ErrorDetail]] = None  # noqa: UP045 -- See ErrorDetail.field.


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: ErrorBody


class ServiceError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
