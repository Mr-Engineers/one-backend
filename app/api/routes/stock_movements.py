from fastapi import APIRouter, Response, status

from app.api.routes.items import fetch_item
from app.core.caller import CallerDep
from app.db.supabase import DbDep, call_mutation
from app.schemas.inventory import StockMovement, StockMovementCreate, StockMovementResult

router = APIRouter()


@router.post(
    "/stock-movements",
    response_model=StockMovementResult,
    status_code=status.HTTP_201_CREATED,
    summary="Consume, adjust or receive without a purchase order",
    description=(
        "Changes the quantity on hand and records the movement. "
        "Not exposed to the agent - the gateway must not allow it."
    ),
    responses={
        200: {"description": "Replay of a request with the same Idempotency-Key."},
        404: {"description": "Item not found."},
        409: {"description": "Not enough stock to consume."},
        422: {"description": "Invalid movement (e.g. adjust without a reason)."},
    },
)
def create_stock_movement(
    body: StockMovementCreate, db: DbDep, caller: CallerDep, response: Response
) -> StockMovementResult:
    result = call_mutation(
        db,
        "record_movement",
        {
            "p_sku": body.sku,
            "p_type": body.type,
            "p_quantity": body.quantity,
            "p_new_quantity": body.new_quantity,
            "p_reason": body.reason,
            "p_idempotency_key": caller.write_idempotency_key(),
        },
        caller=caller,
        action=f"stock.{body.type}",
        audit_input=body.model_dump(exclude_none=True),
    )
    if result["replayed"]:
        response.status_code = status.HTTP_200_OK
    return StockMovementResult(
        movement=StockMovement.model_validate(result["movement"]),
        item=fetch_item(db, body.sku),
    )
