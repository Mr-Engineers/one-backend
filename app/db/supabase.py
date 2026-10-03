"""Database clients and data access."""
import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from postgrest.exceptions import APIError
from supabase import Client, ClientOptions, create_client

from app.core.config import get_settings
from app.core.errors import DEFAULT_CODES, ApiError

# All warehouse tables, views and functions live in this schema.
# It must be on the list of exposed schemas in Supabase (Settings -> API).
DB_SCHEMA = "warehouse"

_ERROR_CODE = re.compile(r"[a-z][a-z_]*")


@lru_cache
def get_supabase_client() -> Client:
    """Return a cached Supabase client bound to the warehouse schema.

    Requires SUPABASE_URL and SUPABASE_KEY to be set.
    """
    settings = get_settings()
    if not settings.supabase_configured:
        raise RuntimeError(
            "Supabase is not configured. Set SUPABASE_URL and SUPABASE_KEY."
        )
    return create_client(settings.supabase_url, settings.supabase_key, options=ClientOptions(schema=DB_SCHEMA))


def get_db() -> Client:
    """FastAPI dependency: the Supabase client, or 503 when it is not configured."""
    if not get_settings().supabase_configured:
        raise ApiError(503, "service_unavailable", "Supabase is not configured. Set SUPABASE_URL and SUPABASE_KEY.")
    return get_supabase_client()


DbDep = Annotated[Client, Depends(get_db)]


def utc_now() -> str:
    """Current time for timestamptz columns without a trigger (e.g. updated_at)."""
    return datetime.now(timezone.utc).isoformat()


def http_status_for(exc: APIError) -> int:
    """HTTP status for a database error.

    SQLSTATE 'PTxxx' maps to status xxx. Constraint violations are client errors.
    """
    code = exc.code or ""
    if code.startswith("PT") and code[2:].isdigit():
        return int(code[2:])
    if code == "23505":  # unique_violation
        return 409
    if code in {"23502", "23503", "23514", "22P02", "22003"}:  # not null, foreign key, check, invalid text, out of range
        return 422
    return 502


def to_http_exception(exc: APIError) -> ApiError:
    """Database error as an API error. A contract error code may come in the HINT."""
    status = http_status_for(exc)
    hint = exc.hint or ""
    code = hint if _ERROR_CODE.fullmatch(hint) else DEFAULT_CODES.get(status, "error")
    message = exc.message if status < 500 else f"Database error: {exc.message}"
    return ApiError(status, code, message)
