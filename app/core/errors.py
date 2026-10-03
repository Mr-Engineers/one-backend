"""Error format from the warehouse API contract: {"error": {"code": "...", "message": "..."}}."""
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

# Code used when an error does not name its own
DEFAULT_CODES = {
    400: "bad_request",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
    502: "database_error",
    503: "service_unavailable",
}


class ApiError(HTTPException):
    """HTTP error with a contract error code, e.g. ApiError(404, "unknown_sku", "SKU X does not exist")."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(status_code=status_code, detail=message)
        self.code = code


class ErrorDetail(BaseModel):
    code: str = Field(..., examples=["unknown_sku"])
    message: str = Field(..., examples=["SKU PAP-A4-80 does not exist"])


class ErrorResponse(BaseModel):
    error: ErrorDetail


ERROR_DESCRIPTIONS = {
    403: "`gateway_required` - only available to proxy-server (Authorization: Bearer).",
    404: "`unknown_sku`, `purchase_order_not_found`, `merchant_not_found` or `unknown_scenario`.",
    409: (
        "`idempotency_conflict`, `invalid_status`, `insufficient_stock`, `sku_exists`, "
        "`marketplace_order_exists` or `stock_changed` (retry)."
    ),
    422: "`validation_error` - invalid fields; `unknown_merchant` - merchant_id not among suppliers.",
}


def error_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI `responses` entries for the given error statuses."""
    return {
        status: {"model": ErrorResponse, "description": ERROR_DESCRIPTIONS.get(status, "Error.")}
        for status in statuses
    }


def error_body(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}


async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = getattr(exc, "code", None) or DEFAULT_CODES.get(exc.status_code, "error")
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(code, str(exc.detail)),
        headers=getattr(exc, "headers", None),
    )


async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    problems = []
    for err in exc.errors():
        location = ".".join(str(part) for part in err["loc"] if part != "body")
        problems.append(f"{location}: {err['msg']}" if location else err["msg"])
    return JSONResponse(status_code=422, content=error_body("validation_error", "; ".join(problems)))


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
