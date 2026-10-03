"""Changing on_hand together with its stock_movements entry.

There is no database function for this, so the update is guarded by the value read
before it (optimistic lock): a concurrent change makes it fail with 409 stock_changed
instead of silently overwriting the other one.
"""
from typing import Any

from postgrest.exceptions import APIError
from supabase import Client

from app.core.caller import Caller
from app.core.errors import ApiError
from app.db.supabase import utc_now


def _stock_changed(sku: str) -> ApiError:
    return ApiError(409, "stock_changed", f"Stock of {sku} changed in the meantime, retry the request")


def current_on_hand(db: Client, sku: str) -> int | None:
    """on_hand of the SKU, or None when it has no stock_levels row yet."""
    rows = db.table("stock_levels").select("on_hand").eq("sku", sku).limit(1).execute().data
    return rows[0]["on_hand"] if rows else None


def record_stock_change(
    db: Client,
    *,
    sku: str,
    movement_type: str,
    on_hand_before: int | None,
    on_hand_after: int,
    caller: Caller,
    reason: str | None = None,
) -> dict[str, Any]:
    """Set on_hand (only if it is still on_hand_before) and append the movement. Returns the movement row."""
    if on_hand_before is None:
        try:
            db.table("stock_levels").insert({"sku": sku, "on_hand": on_hand_after}).execute()
        except APIError as exc:
            if exc.code == "23505":
                raise _stock_changed(sku) from exc
            raise
    else:
        rows = (
            db.table("stock_levels")
            .update({"on_hand": on_hand_after, "updated_at": utc_now()})
            .eq("sku", sku)
            .eq("on_hand", on_hand_before)
            .execute()
            .data
        )
        if not rows:
            raise _stock_changed(sku)

    return (
        db.table("stock_movements")
        .insert(
            {
                "sku": sku,
                "movement_type": movement_type,
                "delta": on_hand_after - (on_hand_before or 0),
                "on_hand_after": on_hand_after,
                "reason": reason,
                "actor": caller.actor,
                "request_id": caller.request_id,
            }
        )
        .execute()
        .data[0]
    )
