-- Demo data comes from scenarios (tables scenarios / scenario_items, migration 003).
-- Loads the happy_path scenario - the same as POST /api/v1/admin/scenarios/happy_path/load.
-- Resets the warehouse: removes purchase orders, stock movements and items outside the scenario.

select load_scenario(
  'happy_path',
  '{"actor": "seed", "via_gateway": false, "action": "scenario.load", "input": {"scenario_id": "happy_path"}}'
);
