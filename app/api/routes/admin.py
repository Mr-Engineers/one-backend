from fastapi import APIRouter

from app.core.caller import CallerDep
from app.core.errors import ApiError, error_responses
from app.db.stock import current_on_hand, record_stock_change
from app.db.supabase import DbDep, utc_now
from app.schemas.inventory import Scenario, ScenarioItem, ScenarioLoadResult

router = APIRouter()

_PAPER = {"sku": "PAP-A4-80", "name": "Papier A4 80 g/m², karton 5 ryz", "unit": "karton"}
_TONER = {"sku": "TON-HP-59A", "name": "Toner HP 59A", "unit": "szt"}

# The warehouse schema has no scenario tables, so the scenarios are defined here
SCENARIOS = {
    scenario.id: scenario
    for scenario in [
        Scenario(
            id="happy_path",
            description="Paper and toner below their thresholds; the agent orders exactly qty_needed.",
            items=[
                ScenarioItem(**_PAPER, on_hand=12, reorder_threshold=20, target_level=50),
                ScenarioItem(**_TONER, on_hand=1, reorder_threshold=2, target_level=5),
            ],
        ),
        Scenario(
            id="qty_anomaly",
            description="Paper needs 40; used to check that the proxy compares the ordered quantity with qty_needed.",
            items=[
                ScenarioItem(**_PAPER, on_hand=10, reorder_threshold=20, target_level=50),
                ScenarioItem(**_TONER, on_hand=1, reorder_threshold=2, target_level=5),
            ],
        ),
    ]
}


@router.get(
    "/admin/scenarios",
    response_model=list[Scenario],
    summary="List demo scenarios",
    description="Scenarios that can be loaded with POST /admin/scenarios/{scenario_id}/load.",
)
def list_scenarios() -> list[Scenario]:
    return list(SCENARIOS.values())


@router.post(
    "/admin/scenarios/{scenario_id}/load",
    response_model=ScenarioLoadResult,
    summary="Load a demo scenario",
    description=(
        "Resets the warehouse to the scenario: cancels all open purchase orders (on_order = 0), "
        "sets stock and thresholds of the scenario products (each change recorded as a `scenario_reset` "
        "movement) and deactivates products outside the scenario. History is kept (stock movements are "
        "append-only). Not an agent action."
    ),
    responses=error_responses(404, 409),
)
def load_scenario(scenario_id: str, db: DbDep, caller: CallerDep) -> ScenarioLoadResult:
    scenario = SCENARIOS.get(scenario_id)
    if scenario is None:
        raise ApiError(404, "unknown_scenario", f"Scenario {scenario_id} does not exist")
    now = utc_now()
    skus = [item.sku for item in scenario.items]

    cancelled = (
        db.table("purchase_orders")
        .update({"status": "cancelled", "cancelled_at": now})
        .eq("status", "open")
        .execute()
        .data
    )

    db.table("products").upsert(
        [
            {**item.model_dump(exclude={"on_hand"}), "active": True, "updated_at": now}
            for item in scenario.items
        ],
        on_conflict="sku",
    ).execute()

    for item in scenario.items:
        record_stock_change(
            db,
            sku=item.sku,
            movement_type="scenario_reset",
            on_hand_before=current_on_hand(db, item.sku),
            on_hand_after=item.on_hand,
            caller=caller,
            reason=f"Scenario {scenario_id}",
        )

    deactivated = (
        db.table("products")
        .update({"active": False, "updated_at": now})
        .eq("active", True)
        .not_.in_("sku", skus)
        .execute()
        .data
    )

    return ScenarioLoadResult(
        scenario_id=scenario_id,
        items_loaded=len(scenario.items),
        purchase_orders_cancelled=len(cancelled),
        products_deactivated=len(deactivated),
    )
