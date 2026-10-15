"""Uniform error body: {code, message, request_id, field_errors?} (spec técnica §6)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

from basketiq.observability.logging import request_id_var

log = logging.getLogger(__name__)


class FieldError(BaseModel):
    field: str
    message: str


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None
    field_errors: list[FieldError] | None = None
    details: dict[str, Any] | None = None


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        field_errors: list[FieldError] | None = None,
        headers: dict[str, str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.field_errors = field_errors
        self.headers = headers
        self.details = details


def not_found(what: str = "Recurso") -> ApiError:
    # Same answer for "does not exist" and "belongs to someone else": no existence oracle.
    return ApiError(404, "not_found", f"{what} não encontrado.")


def _body(
    code: str,
    message: str,
    field_errors: list[FieldError] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return ErrorBody(
        code=code,
        message=message,
        request_id=request_id_var.get(),
        field_errors=field_errors,
        details=details,
    ).model_dump(mode="json", exclude_none=True)


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """For handlers that must attach cookies to an error (e.g. clearing a dead session)."""
    return JSONResponse(status_code=status_code, content=_body(code, message))


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(exc.code, exc.message, exc.field_errors, exc.details),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            FieldError(
                field=".".join(str(p) for p in e["loc"] if p not in ("body", "query", "path")),
                message=e["msg"],
            )
            for e in exc.errors()
        ]
        return JSONResponse(status_code=422, content=_body("validation_error", "Pedido inválido.", errors))

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {401: "unauthenticated", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}.get(
            exc.status_code, "http_error"
        )
        return JSONResponse(status_code=exc.status_code, content=_body(code, str(exc.detail)))

    @app.exception_handler(RateLimitExceeded)
    async def _rate(_: Request, exc: RateLimitExceeded) -> JSONResponse:
        return JSONResponse(
            status_code=429, content=_body("rate_limited", "Demasiados pedidos. Tente novamente mais tarde.")
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", extra={"error_type": type(exc).__name__})
        return JSONResponse(
            status_code=500, content=_body("internal_error", "Erro interno. Tente novamente.")
        )
