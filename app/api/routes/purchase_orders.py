import hashlib
import json
import uuid

from fastapi import APIRouter, Body, status
from supabase import Client

from app.core.caller import CallerDep, GatewayCallerDep
from app.core.errors import ApiError, error_responses
from app.db.supabase import DbDep, call_mutation
from app.schemas.inventory import (
    CancelRequest,
    PurchaseOrder,
    PurchaseOrderCreate,
    PurchaseOrderStatus,
    ReceiveRequest,
)

router = APIRouter()


def _purchase_order_uuid(purchase_order_id: str) -> str:
    """Purchase order IDs are UUIDs; anything else cannot exist."""
    try:
        return str(uuid.UUID(purchase_order_id))
    except ValueError:
        raise ApiError(404, "purchase_order_not_found", f"Purchase order {purchase_order_id} does not exist") from None


def fetch_purchase_order(db: Client, purchase_order_id: str) -> PurchaseOrder:
    rows = (
        db.table("purchase_order_details")
        .select("*")
        .eq("id", _purchase_order_uuid(purchase_order_id))
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        raise ApiError(404, "purchase_order_not_found", f"Purchase order {purchase_order_id} does not exist")
    return PurchaseOrder.from_row(rows[0])


@router.get(
    "/purchase-orders",
    response_model=list[PurchaseOrder],
    summary="List purchase orders (web)",
    description="Newest first. Filter by status, e.g. `open` for deliveries still on the way.",
)
def list_purchase_orders(db: DbDep, status: PurchaseOrderStatus | None = None) -> list[PurchaseOrder]:
    query = db.table("purchase_order_details").select("*")
    if status:
        query = query.eq("status", status)
    rows = query.order("created_at", desc=True).execute().data
    return [PurchaseOrder.from_row(row) for row in rows]


@router.get(
    "/purchase-orders/{purchase_order_id}",
    response_model=PurchaseOrder,
    summary="Get one purchase order (web)",
    responses=error_responses(404),
)
def get_purchase_order(purchase_order_id: str, db: DbDep) -> PurchaseOrder:
    return fetch_purchase_order(db, purchase_order_id)


@router.post(
    "/purchase-orders",
    response_model=PurchaseOrder,
    status_code=status.HTTP_201_CREATED,
    summary="Register an order placed in the marketplace",
    description=(
        "Raises `on_order` for the SKU, so it disappears from `GET /low-stock`; `on_hand` does not change. "
        "Requires `Idempotency-Key`: repeating a request with the same key and body returns the same order "
        "(201, same `id`) without creating a new one. Only available to proxy-server (Authorization: Bearer)."
    ),
    responses=error_responses(403, 404, 409, 422),
)
def create_purchase_order(body: PurchaseOrderCreate, db: DbDep, caller: GatewayCallerDep) -> PurchaseOrder:
    idempotency_key = caller.write_idempotency_key(required=True)
    payload = body.model_dump(mode="json")
    request_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    result = call_mutation(
        db,
        "create_purchase_order",
        {
            "p_sku": body.sku,
            "p_quantity": body.quantity,
            "p_unit_price_amount": body.unit_price.amount,
            "p_currency": body.unit_price.currency,
            "p_marketplace_order_id": body.supplier.marketplace_order_id,
            "p_merchant_id": body.supplier.merchant_id,
            "p_idempotency_key": idempotency_key,
            "p_request_hash": request_hash,
        },
        caller=caller,
        action="purchase_order.create",
        audit_input=payload,
    )
    return PurchaseOrder.from_row(result["purchase_order"])


@router.post(
    "/purchase-orders/{purchase_order_id}/receive",
    response_model=PurchaseOrder,
    summary="Receive a delivery (web / demo)",
    description=(
        "`on_hand += quantity`, `on_order -= quantity`; when everything arrived `status = received`. "
        "Without a body the whole outstanding quantity is received; `{\"quantity\": n}` receives part of it "
        "(the order stays `open`). Not an agent action."
    ),
    responses=error_responses(404, 409, 422),
)
def receive_purchase_order(
    purchase_order_id: str,
    db: DbDep,
    caller: CallerDep,
    body: ReceiveRequest | None = Body(None),
) -> PurchaseOrder:
    po_id = _purchase_order_uuid(purchase_order_id)
    quantity = body.quantity if body else None
    result = call_mutation(
        db,
        "receive_purchase_order",
        {"p_purchase_order_id": po_id, "p_quantity": quantity, "p_idempotency_key": caller.write_idempotency_key()},
        caller=caller,
        action="purchase_order.receive",
        audit_input={"purchase_order_id": po_id, "quantity": quantity},
    )
    return PurchaseOrder.from_row(result["purchase_order"])


@router.post(
    "/purchase-orders/{purchase_order_id}/cancel",
    response_model=PurchaseOrder,
    summary="Cancel a purchase order (web)",
    description=(
        "Only `open` orders. The not yet delivered rest stops counting as `on_order`, "
        "so the product can show up in low-stock again."
    ),
    responses=error_responses(404, 409, 422),
)
def cancel_purchase_order(purchase_order_id: str, body: CancelRequest, db: DbDep, caller: CallerDep) -> PurchaseOrder:
    po_id = _purchase_order_uuid(purchase_order_id)
    result = call_mutation(
        db,
        "cancel_purchase_order",
        {"p_purchase_order_id": po_id, "p_reason": body.reason},
        caller=caller,
        action="purchase_order.cancel",
        audit_input={"purchase_order_id": po_id, "reason": body.reason},
    )
    return PurchaseOrder.from_row(result["purchase_order"])
