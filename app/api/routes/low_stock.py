from datetime import datetime, timezone

from fastapi import APIRouter

from app.db.supabase import DbDep
from app.schemas.inventory import LowStockItem, LowStockResponse

router = APIRouter()


@router.get(
    "/low-stock",
    response_model=LowStockResponse,
    summary="Products to restock",
    description=(
        "Products with `on_hand + on_order < reorder_threshold`. "
        "`qty_needed = max(target_level - on_hand - on_order, 0)`. An empty `items` list means nothing to order. "
        "Consumer: purchasing-agent through proxy-server."
    ),
)
def list_low_stock(db: DbDep) -> LowStockResponse:
    rows = db.table("low_stock").select("*").order("sku").execute().data
    return LowStockResponse(
        items=[LowStockItem.model_validate(row) for row in rows],
        generated_at=datetime.now(timezone.utc),
    )
