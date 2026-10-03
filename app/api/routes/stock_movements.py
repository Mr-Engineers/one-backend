from fastapi import APIRouter, status

from app.api.routes.items import fetch_item
from app.core.caller import CallerDep
from app.core.errors import error_responses
from app.db.supabase import DbDep, call_mutation
from app.schemas.inventory import StockMovement, StockMovementCreate, StockMovementResult

router = APIRouter()


@router.post(
    "/stock-movements",
    response_model=StockMovementResult,
    status_code=status.HTTP_201_CREATED,
    summary="Consume, adjust or receive without a purchase order (web)",
    description=(
        "Changes on_hand and records the movement. Not an agent action. "
        "Repeating a request with the same Idempotency-Key returns the first result."
    ),
    responses=error_responses(404, 409, 422),
)
def create_stock_movement(body: StockMovementCreate, db: DbDep, caller: CallerDep) -> StockMovementResult:
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
    return StockMovementResult(
        movement=StockMovement.model_validate(result["movement"]),
        item=fetch_item(db, body.sku),
    )
