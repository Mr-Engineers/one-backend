"""Database clients and data access."""
import logging
from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from postgrest.exceptions import APIError
from supabase import Client, create_client

from app.core.caller import Caller
from app.core.config import get_settings

logger = logging.getLogger(__name__)


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
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase is not configured. Set SUPABASE_URL and SUPABASE_KEY.",
        )
    return get_supabase_client()


DbDep = Annotated[Client, Depends(get_db)]


def http_status_for(exc: APIError) -> int:
    """HTTP status for a database error.

    Functions raise SQLSTATE 'PTxxx' (see supabase/migrations/002_inventory_functions.sql),
    which maps to status xxx. Constraint violations are client errors.
    """
    code = exc.code or ""
    if code.startswith("PT") and code[2:].isdigit():
        return int(code[2:])
    if code == "23505":  # unique_violation
        return status.HTTP_409_CONFLICT
    if code in {"23502", "23514", "22P02", "22003"}:  # not null, check, invalid text, out of range
        return 422
    return status.HTTP_502_BAD_GATEWAY


def to_http_exception(exc: APIError) -> HTTPException:
    code = http_status_for(exc)
    detail = exc.message if code < 500 else f"Database error: {exc.message}"
    return HTTPException(status_code=code, detail=detail)


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
        http_exc = to_http_exception(exc)
        _audit_failure(db, audit, http_exc)
        raise http_exc from exc


def _audit_failure(db: Client, audit: dict[str, Any], exc: HTTPException) -> None:
    try:
        db.table("audit_log").insert(
            {
                "actor": audit["actor"],
                "via_gateway": audit["via_gateway"],
                "action": audit["action"],
                "request_id": audit["request_id"],
                "input": audit["input"],
                "result": {"status_code": exc.status_code, "detail": exc.detail},
                "status": "rejected" if exc.status_code < 500 else "error",
            }
        ).execute()
    except Exception:
        logger.exception("Could not write audit_log entry for %s", audit["action"])
