from typing import Annotated

from fastapi import APIRouter, Query, status
from supabase import Client

from app.core.caller import CallerDep
from app.core.errors import ApiError, error_responses
from app.db.supabase import DbDep, call_mutation
from app.schemas.inventory import Item, ItemCreate, ItemUpdate, MovementType, StockMovement

router = APIRouter()


def fetch_item(db: Client, sku: str) -> Item:
    """Product from the item_stock view (with on_order / is_low), or 404 unknown_sku."""
    rows = db.table("item_stock").select("*").eq("sku", sku).limit(1).execute().data
    if not rows:
        raise ApiError(404, "unknown_sku", f"SKU {sku} does not exist")
    return Item.model_validate(rows[0])


def _search_term(q: str) -> str:
    # Characters with a meaning in a PostgREST or=() filter
    return "".join(ch for ch in q if ch not in ',()"*\\')


@router.get(
    "/items",
    response_model=list[Item],
    summary="List products (web)",
    description="All products with on_hand, on_order, thresholds and location.",
)
def list_items(
    db: DbDep,
    location: str | None = None,
    category: str | None = None,
    q: Annotated[str | None, Query(description="Search in name and SKU.")] = None,
    below_threshold: Annotated[bool, Query(description="Only products with on_hand + on_order < reorder_threshold.")] = False,
) -> list[Item]:
    query = db.table("item_stock").select("*")
    if location:
        query = query.eq("location", location)
    if category:
        query = query.eq("category", category)
    if q and (term := _search_term(q)):
        query = query.or_(f"name.ilike.*{term}*,sku.ilike.*{term}*")
    if below_threshold:
        query = query.eq("is_low", True)
    return [Item.model_validate(row) for row in query.order("sku").execute().data]


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
    type: MovementType | None = None,
    since: Annotated[str | None, Query(description="ISO date/time, e.g. 2026-10-01.")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[StockMovement]:
    fetch_item(db, sku)
    query = db.table("stock_movement_details").select("*").eq("sku", sku)
    if type:
        query = query.eq("type", type)
    if since:
        query = query.gte("created_at", since)
    rows = query.order("created_at", desc=True).limit(limit).execute().data
    return [StockMovement.model_validate(row) for row in rows]


@router.post(
    "/items",
    response_model=Item,
    status_code=status.HTTP_201_CREATED,
    summary="Add a product (web)",
    responses=error_responses(409, 422),
)
def create_item(body: ItemCreate, db: DbDep, caller: CallerDep) -> Item:
    call_mutation(
        db,
        "create_item",
        {"p_item": body.model_dump(exclude={"initial_on_hand"}), "p_initial_quantity": body.initial_on_hand},
        caller=caller,
        action="item.create",
        audit_input=body.model_dump(),
    )
    return fetch_item(db, body.sku)


@router.patch(
    "/items/{sku}",
    response_model=Item,
    summary="Change product details (web)",
    description="Name, unit, location, thresholds. on_hand changes only through stock movements.",
    responses=error_responses(404, 422),
)
def update_item(sku: str, body: ItemUpdate, db: DbDep, caller: CallerDep) -> Item:
    changes = body.model_dump(exclude_unset=True)
    call_mutation(
        db,
        "update_item",
        {"p_sku": sku, "p_changes": changes},
        caller=caller,
        action="item.update",
        audit_input={"sku": sku, **changes},
    )
    return fetch_item(db, sku)
