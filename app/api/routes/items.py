from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from supabase import Client

from app.core.caller import CallerDep
from app.core.errors import ApiError, error_responses
from app.db.stock import record_stock_change
from app.db.supabase import DbDep, utc_now
from app.schemas.inventory import Item, ItemCreate, ItemUpdate, MovementType, StockMovement

router = APIRouter()

INITIAL_STOCK_REASON = "Initial stock"


def _with_products(db: Client, stock_rows: list[dict[str, Any]]) -> list[Item]:
    """stock_availability rows joined with products (max_order_qty, timestamps)."""
    if not stock_rows:
        return []
    skus = [row["sku"] for row in stock_rows]
    products = {
        row["sku"]: row
        for row in db.table("products").select("sku,max_order_qty,created_at,updated_at").in_("sku", skus).execute().data
    }
    return [Item.from_rows(row, products[row["sku"]]) for row in stock_rows if row["sku"] in products]


def fetch_item(db: Client, sku: str) -> Item:
    """Active product with its stock, or 404 unknown_sku."""
    rows = db.table("stock_availability").select("*").eq("sku", sku).limit(1).execute().data
    items = _with_products(db, rows)
    if not items:
        raise ApiError(404, "unknown_sku", f"SKU {sku} does not exist")
    return items[0]


def _search_term(q: str) -> str:
    # Characters with a meaning in a PostgREST or=() filter
    return "".join(ch for ch in q if ch not in ',()"*\\')


@router.get(
    "/items",
    response_model=list[Item],
    summary="List products (web)",
    description="Active products with on_hand, on_order, qty_needed and thresholds.",
)
def list_items(
    db: DbDep,
    q: Annotated[str | None, Query(description="Search in name and SKU.")] = None,
    below_threshold: Annotated[bool, Query(description="Only products with on_hand + on_order < reorder_threshold.")] = False,
) -> list[Item]:
    query = db.table("stock_availability").select("*")
    if q and (term := _search_term(q)):
        query = query.or_(f"name.ilike.*{term}*,sku.ilike.*{term}*")
    items = _with_products(db, query.order("sku").execute().data)
    return [item for item in items if item.is_low] if below_threshold else items


@router.get(
    "/items/{sku}",
    response_model=Item,
    summary="Get one product (web)",
    responses=error_responses(404),
)
def get_item(sku: str, db: DbDep) -> Item:
    return fetch_item(db, sku)


@router.get(
    "/items/{sku}/movements",
    response_model=list[StockMovement],
    summary="Stock movements of a product (web)",
    description="History of on_hand changes, newest first.",
    responses=error_responses(404),
)
def list_item_movements(
    sku: str,
    db: DbDep,
    movement_type: MovementType | None = None,
    since: Annotated[str | None, Query(description="ISO date/time, e.g. 2026-10-01.")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[StockMovement]:
    fetch_item(db, sku)
    query = db.table("stock_movements").select("*").eq("sku", sku)
    if movement_type:
        query = query.eq("movement_type", movement_type)
    if since:
        query = query.gte("created_at", since)
    rows = query.order("created_at", desc=True).order("id", desc=True).limit(limit).execute().data
    return [StockMovement.model_validate(row) for row in rows]


@router.post(
    "/items",
    response_model=Item,
    status_code=status.HTTP_201_CREATED,
    summary="Add a product (web)",
    description="Creates the product and its stock level; initial_on_hand > 0 is recorded as an adjustment.",
    responses=error_responses(409, 422),
)
def create_item(body: ItemCreate, db: DbDep, caller: CallerDep) -> Item:
    if db.table("products").select("sku").eq("sku", body.sku).limit(1).execute().data:
        raise ApiError(409, "sku_exists", f"SKU {body.sku} already exists")

    db.table("products").insert(body.model_dump(exclude={"initial_on_hand"})).execute()
    if body.initial_on_hand:
        record_stock_change(
            db,
            sku=body.sku,
            movement_type="adjustment",
            on_hand_before=None,
            on_hand_after=body.initial_on_hand,
            caller=caller,
            reason=INITIAL_STOCK_REASON,
        )
    else:
        db.table("stock_levels").insert({"sku": body.sku, "on_hand": 0}).execute()
    return fetch_item(db, body.sku)


@router.patch(
    "/items/{sku}",
    response_model=Item,
    summary="Change product details (web)",
    description="Name, unit, thresholds, max_order_qty. on_hand changes only through stock movements.",
    responses=error_responses(404, 422),
)
def update_item(sku: str, body: ItemUpdate, db: DbDep) -> Item:
    changes = body.model_dump(exclude_unset=True)
    rows = db.table("products").update({**changes, "updated_at": utc_now()}).eq("sku", sku).eq("active", True).execute().data
    if not rows:
        raise ApiError(404, "unknown_sku", f"SKU {sku} does not exist")
    return fetch_item(db, sku)
