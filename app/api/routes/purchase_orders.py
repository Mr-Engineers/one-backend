import hashlib
import json
import secrets
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, status
from postgrest.exceptions import APIError
from supabase import Client

from app.core.caller import CallerDep, GatewayCallerDep
from app.core.errors import ApiError, error_responses
from app.db.supabase import DbDep, utc_now
from app.schemas.inventory import PurchaseOrder, PurchaseOrderCreate, PurchaseOrderStatus

router = APIRouter()


def _not_found(purchase_order_id: str) -> ApiError:
    return ApiError(404, "purchase_order_not_found", f"Purchase order {purchase_order_id} does not exist")


def _invalid_status(purchase_order_id: str, current: str) -> ApiError:
    return ApiError(409, "invalid_status", f"Purchase order {purchase_order_id} is {current}, not open")


def _fetch_row(db: Client, column: str, value: str) -> dict[str, Any] | None:
    rows = db.table("purchase_orders").select("*").eq(column, value).limit(1).execute().data
    return rows[0] if rows else None


def fetch_purchase_order(db: Client, purchase_order_id: str) -> dict[str, Any]:
    row = _fetch_row(db, "id", purchase_order_id)
    if row is None:
        raise _not_found(purchase_order_id)
    return row


def _new_id() -> str:
    return f"po_{secrets.token_hex(4)}"


def _replay(existing: dict[str, Any], request_hash: str) -> PurchaseOrder:
    """Same Idempotency-Key: the same body returns the first order, a different one is a conflict."""
    if existing["request_hash"] != request_hash:
        raise ApiError(409, "idempotency_conflict", "Idempotency-Key was already used with a different request body")
    return PurchaseOrder.from_row(existing)


@router.get(
    "/purchase-orders",
    response_model=list[PurchaseOrder],
    summary="List purchase orders (web)",
    description="Newest first. Filter by status, e.g. `open` for deliveries still on the way.",
)
def list_purchase_orders(
    db: DbDep,
    status: PurchaseOrderStatus | None = None,
    sku: str | None = None,
) -> list[PurchaseOrder]:
    query = db.table("purchase_orders").select("*")
    if status:
        query = query.eq("status", status)
    if sku:
        query = query.eq("sku", sku)
    rows = query.order("created_at", desc=True).execute().data
    return [PurchaseOrder.from_row(row) for row in rows]


@router.get(
    "/purchase-orders/{purchase_order_id}",
    response_model=PurchaseOrder,
    summary="Get one purchase order (web)",
    responses=error_responses(404),
)
def get_purchase_order(purchase_order_id: str, db: DbDep) -> PurchaseOrder:
    return PurchaseOrder.from_row(fetch_purchase_order(db, purchase_order_id))


@router.post(
    "/purchase-orders",
    response_model=PurchaseOrder,
    status_code=status.HTTP_201_CREATED,
    summary="Register an order placed in the marketplace",
    description=(
        "Raises `on_order` for the SKU, so it disappears from `GET /low-stock`; `on_hand` does not change. "
        "Requires `Idempotency-Key` (UUID): repeating a request with the same key and body returns the same order "
        "(201, same `id`) without creating a new one. Only available to proxy-server (Authorization: Bearer)."
    ),
    responses=error_responses(403, 404, 409, 422),
)
def create_purchase_order(body: PurchaseOrderCreate, db: DbDep, caller: GatewayCallerDep) -> PurchaseOrder:
    idempotency_key = caller.idempotency_uuid()
    payload = body.model_dump(mode="json")
    request_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    if existing := _fetch_row(db, "idempotency_key", idempotency_key):
        return _replay(existing, request_hash)

    if not db.table("products").select("sku").eq("sku", body.sku).eq("active", True).limit(1).execute().data:
        raise ApiError(404, "unknown_sku", f"SKU {body.sku} does not exist")
    merchant_id = body.supplier.merchant_id
    if not db.table("suppliers").select("merchant_id").eq("merchant_id", merchant_id).limit(1).execute().data:
        raise ApiError(422, "unknown_merchant", f"Merchant {merchant_id} is not a known supplier")

    unit_price = Decimal(body.unit_price.amount)
    row = {
        "id": _new_id(),
        "sku": body.sku,
        "quantity": body.quantity,
        "unit_price": body.unit_price.amount,
        "currency": body.unit_price.currency,
        # No exchange rates in the warehouse: converted only when the order is already in EUR
        "total_eur": str(unit_price * body.quantity) if body.unit_price.currency == "EUR" else None,
        "marketplace_order_id": body.supplier.marketplace_order_id,
        "merchant_id": merchant_id,
        "idempotency_key": idempotency_key,
        "request_hash": request_hash,
        "request_id": caller.request_id,
        "on_behalf_of": caller.on_behalf_of,
    }
    try:
        created = db.table("purchase_orders").insert(row).execute().data[0]
    except APIError as exc:
        if exc.code != "23505":
            raise
        # A concurrent retry with the same key won the race
        if existing := _fetch_row(db, "idempotency_key", idempotency_key):
            return _replay(existing, request_hash)
        if _fetch_row(db, "marketplace_order_id", body.supplier.marketplace_order_id):
            raise ApiError(
                409,
                "marketplace_order_exists",
                f"Marketplace order {body.supplier.marketplace_order_id} is already registered",
            ) from exc
        raise
    return PurchaseOrder.from_row(created)


@router.post(
    "/purchase-orders/{purchase_order_id}/receive",
    response_model=PurchaseOrder,
    summary="Receive a delivery (web / demo)",
    description=(
        "The whole order arrives: `status = received`, `on_hand += quantity`, `on_order -= quantity`, "
        "and a `receipt` stock movement is recorded. Only `open` orders. Not an agent action."
    ),
    responses=error_responses(404, 409),
)
def receive_purchase_order(purchase_order_id: str, db: DbDep, caller: CallerDep) -> PurchaseOrder:
    current = fetch_purchase_order(db, purchase_order_id)
    if current["status"] != "open":
        raise _invalid_status(purchase_order_id, current["status"])
    try:
        result = (
            db.rpc(
                "receive_purchase_order",
                {"p_id": purchase_order_id, "p_actor": caller.actor, "p_request_id": caller.request_id},
            )
            .execute()
            .data
        )
    except APIError as exc:
        # RAISE EXCEPTION in the function: received or cancelled in the meantime
        if exc.code == "P0001":
            raise ApiError(409, "invalid_status", exc.message) from exc
        raise
    return PurchaseOrder.from_row(result[0] if isinstance(result, list) else result)


@router.post(
    "/purchase-orders/{purchase_order_id}/cancel",
    response_model=PurchaseOrder,
    summary="Cancel a purchase order (web)",
    description="Only `open` orders. The order stops counting as `on_order`, so the product can show up in low-stock again.",
    responses=error_responses(404, 409),
)
def cancel_purchase_order(purchase_order_id: str, db: DbDep) -> PurchaseOrder:
    rows = (
        db.table("purchase_orders")
        .update({"status": "cancelled", "cancelled_at": utc_now()})
        .eq("id", purchase_order_id)
        .eq("status", "open")
        .execute()
        .data
    )
    if not rows:
        current = fetch_purchase_order(db, purchase_order_id)
        raise _invalid_status(purchase_order_id, current["status"])
    return PurchaseOrder.from_row(rows[0])
