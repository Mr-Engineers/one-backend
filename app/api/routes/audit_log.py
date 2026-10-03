from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.db.supabase import DbDep
from app.schemas.inventory import AuditEntry

router = APIRouter()


@router.get(
    "/audit-log",
    response_model=list[AuditEntry],
    summary="Audit log (staff)",
    description="Every change (and every rejected or failed attempt), newest first.",
)
def list_audit_log(
    db: DbDep,
    actor: str | None = None,
    action: Annotated[str | None, Query(examples=["purchase_order.create"])] = None,
    request_id: str | None = None,
    status: Literal["ok", "rejected", "error"] | None = None,
    since: Annotated[str | None, Query(description="ISO date/time, e.g. 2026-10-01.")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[AuditEntry]:
    query = db.table("audit_log").select("*")
    if actor:
        query = query.eq("actor", actor)
    if action:
        query = query.eq("action", action)
    if request_id:
        query = query.eq("request_id", request_id)
    if status:
        query = query.eq("status", status)
    if since:
        query = query.gte("created_at", since)
    rows = query.order("id", desc=True).limit(limit).execute().data
    return [AuditEntry.model_validate(row) for row in rows]
