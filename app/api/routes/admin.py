from fastapi import APIRouter

from app.core.caller import CallerDep
from app.core.errors import error_responses
from app.db.supabase import DbDep, call_mutation
from app.schemas.inventory import Scenario, ScenarioItem, ScenarioLoadResult

router = APIRouter()


@router.get(
    "/admin/scenarios",
    response_model=list[Scenario],
    summary="List demo scenarios",
    description="Scenarios that can be loaded with POST /admin/scenarios/{scenario_id}/load.",
)
def list_scenarios(db: DbDep) -> list[Scenario]:
    scenarios = db.table("scenarios").select("*").order("id").execute().data
    items = db.table("scenario_items").select("*").order("sku").execute().data
    return [
        Scenario(
            id=scenario["id"],
            description=scenario["description"],
            items=[ScenarioItem.model_validate(item) for item in items if item["scenario_id"] == scenario["id"]],
        )
        for scenario in scenarios
    ]


@router.post(
    "/admin/scenarios/{scenario_id}/load",
    response_model=ScenarioLoadResult,
    summary="Load a demo scenario",
    description=(
        "Resets the warehouse to the scenario: removes all purchase orders and stock movements and "
        "products outside the scenario, sets stock and thresholds of the scenario products. "
        "The audit log is kept. Not an agent action."
    ),
    responses=error_responses(404),
)
def load_scenario(scenario_id: str, db: DbDep, caller: CallerDep) -> ScenarioLoadResult:
    result = call_mutation(
        db,
        "load_scenario",
        {"p_scenario_id": scenario_id},
        caller=caller,
        action="scenario.load",
        audit_input={"scenario_id": scenario_id},
    )
    return ScenarioLoadResult.model_validate(result)
