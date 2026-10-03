from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Response, status

from app.core.caller import CallerDep, GatewayCallerDep
from app.db.supabase import DbDep, call_mutation
from app.schemas.inventory import (
    CancelRequest,
    PurchaseOrder,
    PurchaseOrderCreate,
    PurchaseOrderStatus,
    ReceiveRequest,
    ReceiveResult,
)

router = APIRouter()


@router.get(
    "/purchase-orders",
    response_model=list[PurchaseOrder],
    summary="List purchase orders (list_purchase_orders)",
    description="Newest first. Filter by status, e.g. `ordered` for deliveries still on the way.",
)
def list_purchase_orders(db: DbDep, status: PurchaseOrderStatus | None = None) -> list[PurchaseOrder]:
    query = db.table("purchase_order_details").select("*")
    if status:
        query = query.eq("status", status)
    rows = query.order("created_at", desc=True).execute().data
    return [PurchaseOrder.model_validate(row) for row in rows]


@router.get(
    "/purchase-orders/{purchase_order_id}",
    response_model=PurchaseOrder,
    summary="Get one purchase order (get_purchase_order)",
    responses={404: {"description": "Purchase order not found."}},
)
def get_purchase_order(purchase_order_id: UUID, db: DbDep) -> PurchaseOrder:
    rows = (
        db.table("purchase_order_details")
        .select("*")
        .eq("id", str(purchase_order_id))
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Purchase order {purchase_order_id} not found",
        )
    return PurchaseOrder.model_validate(rows[0])


@router.post(
    "/purchase-orders",
    response_model=PurchaseOrder,
    status_code=status.HTTP_201_CREATED,
    summary="Register an order placed in the shop (create_purchase_order)",
    description=(
        "Call after the order was placed in the shop (backend-2), with the shop's order ID. "
        "Does not change quantity on hand; the ordered amounts show up as `on_order` in low-stock. "
        "Only available through the gateway."
    ),
    responses={
        200: {"description": "Replay of a request with the same Idempotency-Key."},
        403: {"description": "Not called through the gateway."},
        404: {"description": "An item does not exist."},
        409: {"description": "Idempotency-Key reused for a different order."},
        422: {"description": "Invalid order (e.g. an item without shop_sku)."},
    },
)
def create_purchase_order(
    body: PurchaseOrderCreate, db: DbDep, caller: GatewayCallerDep, response: Response
) -> PurchaseOrder:
    result = call_mutation(
        db,
        "create_purchase_order",
        {
            "p_shop_order_id": body.shop_order_id,
            "p_lines": [line.model_dump() for line in body.lines],
            "p_idempotency_key": caller.write_idempotency_key(),
        },
        caller=caller,
        action="purchase_order.create",
        audit_input=body.model_dump(),
    )
    if result["replayed"]:
        response.status_code = status.HTTP_200_OK
    return PurchaseOrder.model_validate(result["purchase_order"])


@router.post(
    "/purchase-orders/{purchase_order_id}/receive",
    response_model=ReceiveResult,
    summary="Receive a delivery (receive_purchase_order)",
    description=(
        "Puts delivered goods on stock. Without `lines` everything still outstanding is received; "
        "with `lines` only the given amounts (partial delivery)."
    ),
    responses={
        404: {"description": "Purchase order not found."},
        409: {"description": "Purchase order already received or cancelled."},
        422: {"description": "More than outstanding, or an item not in the order."},
    },
)
def receive_purchase_order(
    purchase_order_id: UUID,
    db: DbDep,
    caller: CallerDep,
    body: ReceiveRequest | None = Body(None),
) -> ReceiveResult:
    lines = [line.model_dump() for line in body.lines] if body and body.lines else None
    result = call_mutation(
        db,
        "receive_purchase_order",
        {
            "p_purchase_order_id": str(purchase_order_id),
            "p_lines": lines,
            "p_idempotency_key": caller.write_idempotency_key(),
        },
        caller=caller,
        action="purchase_order.receive",
        audit_input={"purchase_order_id": str(purchase_order_id), "lines": lines},
    )
    return ReceiveResult.model_validate(result)


@router.post(
    "/purchase-orders/{purchase_order_id}/cancel",
    response_model=PurchaseOrder,
    summary="Cancel a purchase order (staff)",
    description="The not yet delivered rest stops counting as on order, so the items show up in low-stock again.",
    responses={
        404: {"description": "Purchase order not found."},
        409: {"description": "Purchase order already received or cancelled."},
    },
)
def cancel_purchase_order(
    purchase_order_id: UUID, body: CancelRequest, db: DbDep, caller: CallerDep
) -> PurchaseOrder:
    result = call_mutation(
        db,
        "cancel_purchase_order",
        {"p_purchase_order_id": str(purchase_order_id), "p_reason": body.reason},
        caller=caller,
        action="purchase_order.cancel",
        audit_input={"purchase_order_id": str(purchase_order_id), "reason": body.reason},
    )
    return PurchaseOrder.model_validate(result["purchase_order"])
