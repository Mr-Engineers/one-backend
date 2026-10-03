"""Database clients and data access."""
import logging
import re
from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends
from postgrest.exceptions import APIError
from supabase import Client, create_client

from app.core.caller import Caller
from app.core.config import get_settings
from app.core.errors import DEFAULT_CODES, ApiError

logger = logging.getLogger(__name__)

_ERROR_CODE = re.compile(r"[a-z][a-z_]*")


@lru_cache
def get_supabase_client() -> Client:
    """Return a cached Supabase client.

    Requires SUPABASE_URL and SUPABASE_KEY to be set.
    """
    settings = get_settings()
    if not settings.supabase_configured:
        raise RuntimeError(
            "Supabase is not configured. Set SUPABASE_URL and SUPABASE_KEY."
        )
    return create_client(settings.supabase_url, settings.supabase_key)


def get_db() -> Client:
    """FastAPI dependency: the Supabase client, or 503 when it is not configured."""
    if not get_settings().supabase_configured:
        raise ApiError(503, "service_unavailable", "Supabase is not configured. Set SUPABASE_URL and SUPABASE_KEY.")
    return get_supabase_client()


DbDep = Annotated[Client, Depends(get_db)]


def http_status_for(exc: APIError) -> int:
    """HTTP status for a database error.

    Functions raise SQLSTATE 'PTxxx' (see supabase/migrations/003_warehouse_contract.sql),
    which maps to status xxx. Constraint violations are client errors.
    """
    code = exc.code or ""
    if code.startswith("PT") and code[2:].isdigit():
        return int(code[2:])
    if code == "23505":  # unique_violation
        return 409
    if code in {"23502", "23514", "22P02", "22003"}:  # not null, check, invalid text, out of range
        return 422
    return 502


def to_http_exception(exc: APIError) -> ApiError:
    """Database error as an API error. Functions put the contract error code in the HINT."""
    status = http_status_for(exc)
    hint = exc.hint or ""
    code = hint if _ERROR_CODE.fullmatch(hint) else DEFAULT_CODES.get(status, "error")
    message = exc.message if status < 500 else f"Database error: {exc.message}"
    return ApiError(status, code, message)


def call_mutation(
    db: Client,
    function: str,
    params: dict[str, Any],
    *,
    caller: Caller,
    action: str,
    audit_input: dict[str, Any],
) -> dict[str, Any]:
    """Call a database function that changes data.

    The function writes the audit_log row for a successful change in its own
    transaction. A rejected or failed call is rolled back there, so it is audited here.
    """
    audit = caller.audit(action, audit_input)
    try:
        return db.rpc(function, {**params, "p_audit": audit}).execute().data
    except APIError as exc:
        api_exc = to_http_exception(exc)
        _audit_failure(db, audit, api_exc)
        raise api_exc from exc


def _audit_failure(db: Client, audit: dict[str, Any], exc: ApiError) -> None:
    try:
        db.table("audit_log").insert(
            {
                "actor": audit["actor"],
                "via_gateway": audit["via_gateway"],
                "action": audit["action"],
                "request_id": audit["request_id"],
                "input": audit["input"],
                "result": {"status_code": exc.status_code, "code": exc.code, "message": exc.detail},
                "status": "rejected" if exc.status_code < 500 else "error",
            }
        ).execute()
    except Exception:
        logger.exception("Could not write audit_log entry for %s", audit["action"])
