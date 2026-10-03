from typing import Annotated

from fastapi import APIRouter, Query

from app.db.supabase import DbDep
from app.schemas.inventory import LowStockItem

router = APIRouter()


@router.get(
    "/low-stock",
    response_model=list[LowStockItem],
    summary="Items to restock (list_low_stock)",
    description=(
        "Items below min_qty with `suggested_qty = max_qty - quantity - on_order`. "
        "By default items already fully on order are left out, so the agent does not order them twice."
    ),
)
def list_low_stock(
    db: DbDep,
    include_on_order: Annotated[
        bool, Query(description="Also list items whose shortage is already fully on order.")
    ] = False,
    orderable_only: Annotated[
        bool, Query(description="Only items mapped to a shop product (shop_sku set).")
    ] = False,
) -> list[LowStockItem]:
    query = db.table("low_stock").select("*")
    if not include_on_order:
        query = query.gt("suggested_qty", 0)
    if orderable_only:
        query = query.not_.is_("shop_sku", "null")
    rows = query.order("sku").execute().data
    return [LowStockItem.model_validate(row) for row in rows]
