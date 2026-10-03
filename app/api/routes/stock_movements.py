from fastapi import APIRouter, status

from app.api.routes.items import fetch_item
from app.core.caller import CallerDep
from app.core.errors import ApiError, error_responses
from app.db.stock import current_on_hand, record_stock_change
from app.db.supabase import DbDep
from app.schemas.inventory import StockMovement, StockMovementCreate, StockMovementResult

router = APIRouter()


@router.post(
    "/stock-movements",
    response_model=StockMovementResult,
    status_code=status.HTTP_201_CREATED,
    summary="Issue from stock or adjust after a count (web)",
    description=(
        "Changes on_hand and records the movement. Not an agent action. "
        "Deliveries are recorded by receiving a purchase order. "
        "409 `stock_changed` means another change happened at the same time - retry."
    ),
    responses=error_responses(404, 409, 422),
)
def create_stock_movement(body: StockMovementCreate, db: DbDep, caller: CallerDep) -> StockMovementResult:
    fetch_item(db, body.sku)
    on_hand_before = current_on_hand(db, body.sku)
    on_hand = on_hand_before or 0

    if body.movement_type == "issue":
        on_hand_after = on_hand - body.quantity
        if on_hand_after < 0:
            raise ApiError(409, "insufficient_stock", f"Only {on_hand} of {body.sku} in stock")
    else:
        on_hand_after = body.new_quantity
        if on_hand_after == on_hand:
            raise ApiError(422, "validation_error", f"new_quantity equals the current stock ({on_hand}), nothing to adjust")

    movement = record_stock_change(
        db,
        sku=body.sku,
        movement_type=body.movement_type,
        on_hand_before=on_hand_before,
        on_hand_after=on_hand_after,
        caller=caller,
        reason=body.reason,
    )
    return StockMovementResult(
        movement=StockMovement.model_validate(movement),
        item=fetch_item(db, body.sku),
    )
