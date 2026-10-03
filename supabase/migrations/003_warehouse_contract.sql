-- Aligns the database with the warehouse API contract (Kontrakt API - Magazyn, 2026-10-03):
--   * a purchase order is one SKU (purchase_order_lines is dropped)
--   * SKU is shared with the marketplace (items.shop_sku is dropped)
--   * low stock = on_hand + on_order < reorder_threshold
--   * purchase order status: open / received / cancelled
--   * demo scenarios (scenarios, scenario_items, load_scenario)
--   * error codes from the contract, passed to the API in the error HINT
--
-- Existing purchase orders are deleted (test data). Items and audit_log are kept.
-- Run once, after 001 and 002.

begin;

--------------------------------------------------------------------------------
-- Drop what depends on the old structure
--------------------------------------------------------------------------------

drop view if exists low_stock;
drop view if exists item_stock;
drop view if exists purchase_order_details;

drop function if exists create_purchase_order(text, jsonb, text, jsonb);
drop function if exists receive_purchase_order(uuid, jsonb, text, jsonb);
drop function if exists cancel_purchase_order(uuid, text, jsonb);
drop function if exists _purchase_order_json(uuid);

update stock_movements set purchase_order_id = null where purchase_order_id is not null;
drop table purchase_order_lines;
delete from purchase_orders;

--------------------------------------------------------------------------------
-- items: SKU shared with the marketplace, target level > 0
--------------------------------------------------------------------------------

alter table items drop column shop_sku;
alter table items add constraint items_max_qty_positive check (max_qty > 0);

--------------------------------------------------------------------------------
-- purchase_orders: one SKU per order
--------------------------------------------------------------------------------

alter table purchase_orders rename column shop_order_id to marketplace_order_id;

alter table purchase_orders
  add column item_id           uuid          not null references items(id),
  add column quantity          integer       not null check (quantity > 0),
  add column quantity_received integer       not null default 0,
  add column unit_price_amount numeric(12,2) not null check (unit_price_amount >= 0),
  add column currency          char(3)       not null check (currency ~ '^[A-Z]{3}$'),
  add column merchant_id       text          not null,
  add column request_hash      text,
  add column request_id        text,
  add column cancelled_at      timestamptz,
  add constraint purchase_orders_received_range check (quantity_received between 0 and quantity);

alter table purchase_orders alter column marketplace_order_id set not null;

-- Status: open / received / cancelled (partially received orders stay open)
alter table purchase_orders alter column status drop default;
alter table purchase_orders alter column status type text;
drop type po_status;
create type po_status as enum ('open', 'received', 'cancelled');
alter table purchase_orders alter column status type po_status using status::po_status;
alter table purchase_orders alter column status set default 'open';

create index purchase_orders_open_item_idx on purchase_orders (item_id) where status = 'open';

--------------------------------------------------------------------------------
-- Demo scenarios
--------------------------------------------------------------------------------

create table scenarios (
  id          text primary key,
  description text not null
);

create table scenario_items (
  scenario_id       text    not null references scenarios(id) on delete cascade,
  sku               text    not null,
  name              text    not null,
  unit              text    not null,
  location          text    not null default 'Magazyn',
  on_hand           integer not null check (on_hand >= 0),
  on_order          integer not null default 0 check (on_order >= 0),
  reorder_threshold integer not null check (reorder_threshold >= 0),
  target_level      integer not null check (target_level > 0),
  primary key (scenario_id, sku),
  check (target_level >= reorder_threshold)
);

insert into scenarios (id, description) values
  ('happy_path',  'Paper and toner below their thresholds; the agent orders exactly qty_needed.'),
  ('qty_anomaly', 'Paper needs 40; used to check that the proxy compares the ordered quantity with qty_needed.');

insert into scenario_items (scenario_id, sku, name, unit, on_hand, on_order, reorder_threshold, target_level) values
  ('happy_path',  'PAP-A4-80',  'Papier A4 80 g/m², karton 5 ryz', 'karton', 12, 0, 20, 50),
  ('happy_path',  'TON-HP-59A', 'Toner HP 59A',                    'szt',     1, 0,  2,  5),
  ('qty_anomaly', 'PAP-A4-80',  'Papier A4 80 g/m², karton 5 ryz', 'karton', 10, 0, 20, 50),
  ('qty_anomaly', 'TON-HP-59A', 'Toner HP 59A',                    'szt',     1, 0,  2,  5);

--------------------------------------------------------------------------------
-- Views (columns already named as in the API)
--------------------------------------------------------------------------------

create view item_stock with (security_invoker = true) as
select i.sku,
       i.name,
       i.unit,
       i.category,
       i.location,
       i.quantity                                     as on_hand,
       coalesce(o.on_order, 0)::integer               as on_order,
       i.min_qty                                      as reorder_threshold,
       i.max_qty                                      as target_level,
       i.quantity + coalesce(o.on_order, 0) < i.min_qty as is_low,
       i.created_at,
       i.updated_at
from items i
left join (
  select item_id, sum(quantity - quantity_received) as on_order
  from purchase_orders
  where status = 'open'
  group by item_id
) o on o.item_id = i.id;

-- Contract: on_hand + on_order < reorder_threshold, qty_needed = max(target_level - on_hand - on_order, 0)
create view low_stock with (security_invoker = true) as
select sku, name, unit, on_hand, on_order, reorder_threshold, target_level,
       greatest(target_level - on_hand - on_order, 0) as qty_needed
from item_stock
where is_low;

create view purchase_order_details with (security_invoker = true) as
select p.id,
       i.sku,
       p.quantity,
       p.quantity_received,
       p.status,
       p.unit_price_amount,
       p.currency,
       p.marketplace_order_id,
       p.merchant_id,
       p.created_by,
       p.request_id,
       p.created_at,
       p.received_at,
       p.cancelled_at
from purchase_orders p
join items i on i.id = p.item_id;

--------------------------------------------------------------------------------
-- Errors: HTTP status in SQLSTATE (PTxxx -> PostgREST answers xxx),
-- contract error code in HINT (the API puts it into {"error": {"code": ...}})
--------------------------------------------------------------------------------

create or replace function _fail(p_status integer, p_code text, p_message text)
returns void
language plpgsql
as $$
begin
  raise exception using errcode = 'PT' || p_status, message = p_message, hint = p_code;
end;
$$;

create or replace function _purchase_order_json(p_id uuid)
returns jsonb
language sql
stable
as $$
  select to_jsonb(d) from purchase_order_details d where d.id = p_id;
$$;

--------------------------------------------------------------------------------
-- Purchase orders
--------------------------------------------------------------------------------

-- Registers an order placed in the marketplace. Raises on_order, does not change on_hand.
-- Same Idempotency-Key + same request (p_request_hash) returns the existing order.
create or replace function create_purchase_order(
  p_sku                  text,
  p_quantity             integer,
  p_unit_price_amount    numeric,
  p_currency             text,
  p_marketplace_order_id text,
  p_merchant_id          text,
  p_idempotency_key      text,
  p_request_hash         text,
  p_audit                jsonb
)
returns jsonb
language plpgsql
as $$
declare
  v_po     purchase_orders%rowtype;
  v_item   items%rowtype;
  v_result jsonb;
begin
  if p_idempotency_key is not null then
    select * into v_po from purchase_orders where idempotency_key = p_idempotency_key;
    if found then
      if v_po.request_hash is distinct from p_request_hash then
        perform _fail(409, 'idempotency_conflict', 'Idempotency-Key was already used with a different request body');
      end if;
      return jsonb_build_object('purchase_order', _purchase_order_json(v_po.id), 'replayed', true);
    end if;
  end if;

  select * into v_item from items where sku = p_sku;
  if not found then
    perform _fail(404, 'unknown_sku', format('SKU %s does not exist', p_sku));
  end if;

  begin
    insert into purchase_orders (item_id, quantity, unit_price_amount, currency, marketplace_order_id,
                                 merchant_id, idempotency_key, request_hash, created_by, request_id)
    values (v_item.id, p_quantity, p_unit_price_amount, p_currency, p_marketplace_order_id,
            p_merchant_id, p_idempotency_key, p_request_hash, p_audit->>'actor', p_audit->>'request_id')
    returning * into v_po;
  exception when unique_violation then
    -- The same key arrived concurrently and the other request won
    select * into v_po from purchase_orders where idempotency_key = p_idempotency_key;
    if v_po.request_hash is distinct from p_request_hash then
      perform _fail(409, 'idempotency_conflict', 'Idempotency-Key was already used with a different request body');
    end if;
    return jsonb_build_object('purchase_order', _purchase_order_json(v_po.id), 'replayed', true);
  end;

  v_result := jsonb_build_object('purchase_order', _purchase_order_json(v_po.id), 'replayed', false);
  perform _audit_ok(p_audit, v_result);
  return v_result;
end;
$$;

-- Receives a delivery: p_quantity null = everything still outstanding.
create or replace function receive_purchase_order(
  p_purchase_order_id uuid,
  p_quantity          integer,
  p_idempotency_key   text,
  p_audit             jsonb
)
returns jsonb
language plpgsql
as $$
declare
  v_po          purchase_orders%rowtype;
  v_item        items%rowtype;
  v_movement    stock_movements%rowtype;
  v_outstanding integer;
  v_qty         integer;
  v_result      jsonb;
begin
  if p_idempotency_key is not null then
    select * into v_movement from stock_movements where idempotency_key = p_idempotency_key;
    if found then
      if v_movement.purchase_order_id is distinct from p_purchase_order_id then
        perform _fail(409, 'idempotency_conflict', 'Idempotency-Key was already used with a different request');
      end if;
      return jsonb_build_object('purchase_order', _purchase_order_json(p_purchase_order_id), 'replayed', true);
    end if;
  end if;

  select * into v_po from purchase_orders where id = p_purchase_order_id for update;
  if not found then
    perform _fail(404, 'purchase_order_not_found', format('Purchase order %s does not exist', p_purchase_order_id));
  end if;
  if v_po.status <> 'open' then
    perform _fail(409, 'invalid_status', format('Purchase order is %s', v_po.status));
  end if;

  v_outstanding := v_po.quantity - v_po.quantity_received;
  v_qty := coalesce(p_quantity, v_outstanding);
  if v_qty <= 0 or v_qty > v_outstanding then
    perform _fail(422, 'validation_error',
                  format('Cannot receive %s: %s outstanding', v_qty, v_outstanding));
  end if;

  update items
  set quantity = quantity + v_qty, updated_at = now()
  where id = v_po.item_id
  returning * into v_item;

  insert into stock_movements (item_id, type, quantity_delta, quantity_after, reason,
                               purchase_order_id, actor, request_id, idempotency_key)
  values (v_item.id, 'receive', v_qty, v_item.quantity,
          format('Purchase order %s', v_po.marketplace_order_id),
          v_po.id, p_audit->>'actor', p_audit->>'request_id', p_idempotency_key);

  update purchase_orders
  set quantity_received = quantity_received + v_qty,
      status            = case when quantity_received + v_qty = quantity then 'received'::po_status else status end,
      received_at       = case when quantity_received + v_qty = quantity then now() else received_at end
  where id = v_po.id;

  v_result := jsonb_build_object('purchase_order', _purchase_order_json(v_po.id), 'replayed', false);
  perform _audit_ok(p_audit, v_result);
  return v_result;
end;
$$;

-- Cancels the not yet delivered rest of an open order (received stock stays).
create or replace function cancel_purchase_order(
  p_purchase_order_id uuid,
  p_reason            text,
  p_audit             jsonb
)
returns jsonb
language plpgsql
as $$
declare
  v_po     purchase_orders%rowtype;
  v_result jsonb;
begin
  if nullif(trim(p_reason), '') is null then
    perform _fail(422, 'validation_error', 'reason is required');
  end if;

  select * into v_po from purchase_orders where id = p_purchase_order_id for update;
  if not found then
    perform _fail(404, 'purchase_order_not_found', format('Purchase order %s does not exist', p_purchase_order_id));
  end if;
  if v_po.status <> 'open' then
    perform _fail(409, 'invalid_status', format('Purchase order is %s', v_po.status));
  end if;

  update purchase_orders set status = 'cancelled', cancelled_at = now() where id = v_po.id;

  v_result := jsonb_build_object('purchase_order', _purchase_order_json(v_po.id));
  perform _audit_ok(p_audit, v_result);
  return v_result;
end;
$$;

--------------------------------------------------------------------------------
-- Stock movements and items (same signatures as in 002, contract error codes)
--------------------------------------------------------------------------------

create or replace function record_movement(
  p_sku             text,
  p_type            movement_type,
  p_quantity        integer,      -- consume / receive: amount (> 0)
  p_new_quantity    integer,      -- adjust: quantity counted on the shelf
  p_reason          text,
  p_idempotency_key text,
  p_audit           jsonb
)
returns jsonb
language plpgsql
as $$
declare
  v_item     items%rowtype;
  v_movement stock_movements%rowtype;
  v_delta    integer;
  v_reason   text := nullif(trim(p_reason), '');
  v_result   jsonb;
begin
  if p_idempotency_key is not null then
    select * into v_movement from stock_movements where idempotency_key = p_idempotency_key;
    if found then
      select * into v_item from items where id = v_movement.item_id;
      if v_item.sku <> p_sku or v_movement.type <> p_type then
        perform _fail(409, 'idempotency_conflict', 'Idempotency-Key was already used with a different request');
      end if;
      return jsonb_build_object('movement', to_jsonb(v_movement) || jsonb_build_object('sku', v_item.sku),
                                'replayed', true);
    end if;
  end if;

  select * into v_item from items where sku = p_sku for update;
  if not found then
    perform _fail(404, 'unknown_sku', format('SKU %s does not exist', p_sku));
  end if;

  if p_type = 'adjust' then
    if p_new_quantity is null or p_new_quantity < 0 then
      perform _fail(422, 'validation_error', 'adjust requires new_quantity >= 0');
    end if;
    v_delta := p_new_quantity - v_item.quantity;
    if v_delta = 0 then
      perform _fail(422, 'validation_error',
                    format('SKU %s already has on_hand %s, nothing to adjust', p_sku, v_item.quantity));
    end if;
  else
    if p_quantity is null or p_quantity <= 0 then
      perform _fail(422, 'validation_error', format('%s requires quantity > 0', p_type));
    end if;
    v_delta := case when p_type = 'consume' then -p_quantity else p_quantity end;
  end if;

  if p_type in ('adjust', 'receive') and v_reason is null then
    perform _fail(422, 'validation_error', format('%s requires a reason', p_type));
  end if;

  if v_item.quantity + v_delta < 0 then
    perform _fail(409, 'insufficient_stock',
                  format('Not enough stock of %s: %s on hand, %s requested', p_sku, v_item.quantity, -v_delta));
  end if;

  update items
  set quantity = quantity + v_delta, updated_at = now()
  where id = v_item.id
  returning * into v_item;

  insert into stock_movements (item_id, type, quantity_delta, quantity_after, reason, actor, request_id, idempotency_key)
  values (v_item.id, p_type, v_delta, v_item.quantity, v_reason,
          p_audit->>'actor', p_audit->>'request_id', p_idempotency_key)
  returning * into v_movement;

  v_result := jsonb_build_object('movement', to_jsonb(v_movement) || jsonb_build_object('sku', v_item.sku),
                                 'replayed', false);
  perform _audit_ok(p_audit, v_result);
  return v_result;
end;
$$;

-- p_item: {"sku", "name", "category", "unit", "location", "reorder_threshold", "target_level"}
create or replace function create_item(
  p_item             jsonb,
  p_initial_quantity integer,
  p_audit            jsonb
)
returns jsonb
language plpgsql
as $$
declare
  v_item   items%rowtype;
  v_result jsonb;
begin
  if exists (select 1 from items where sku = p_item->>'sku') then
    perform _fail(409, 'sku_exists', format('SKU %s already exists', p_item->>'sku'));
  end if;

  insert into items (sku, name, category, unit, location, min_qty, max_qty)
  values (
    p_item->>'sku',
    p_item->>'name',
    p_item->>'category',
    p_item->>'unit',
    p_item->>'location',
    (p_item->>'reorder_threshold')::integer,
    (p_item->>'target_level')::integer
  )
  returning * into v_item;

  if coalesce(p_initial_quantity, 0) > 0 then
    update items set quantity = p_initial_quantity where id = v_item.id returning * into v_item;
    insert into stock_movements (item_id, type, quantity_delta, quantity_after, reason, actor, request_id)
    values (v_item.id, 'adjust', p_initial_quantity, p_initial_quantity, 'Initial stock',
            p_audit->>'actor', p_audit->>'request_id');
  end if;

  v_result := jsonb_build_object('sku', v_item.sku);
  perform _audit_ok(p_audit, v_result);
  return v_result;
end;
$$;

-- p_changes: only the fields to change (name, category, unit, location, reorder_threshold, target_level).
-- on_hand is never changed here - use stock movements.
create or replace function update_item(
  p_sku     text,
  p_changes jsonb,
  p_audit   jsonb
)
returns jsonb
language plpgsql
as $$
declare
  v_item   items%rowtype;
  v_result jsonb;
begin
  update items set
    name       = case when p_changes ? 'name'              then p_changes->>'name'                          else name     end,
    category   = case when p_changes ? 'category'          then p_changes->>'category'                      else category end,
    unit       = case when p_changes ? 'unit'              then p_changes->>'unit'                          else unit     end,
    location   = case when p_changes ? 'location'          then p_changes->>'location'                      else location end,
    min_qty    = case when p_changes ? 'reorder_threshold' then (p_changes->>'reorder_threshold')::integer else min_qty  end,
    max_qty    = case when p_changes ? 'target_level'      then (p_changes->>'target_level')::integer      else max_qty  end,
    updated_at = now()
  where sku = p_sku
  returning * into v_item;

  if not found then
    perform _fail(404, 'unknown_sku', format('SKU %s does not exist', p_sku));
  end if;

  v_result := jsonb_build_object('sku', v_item.sku, 'changes', p_changes);
  perform _audit_ok(p_audit, v_result);
  return v_result;
end;
$$;

--------------------------------------------------------------------------------
-- Demo: load a scenario
--------------------------------------------------------------------------------

-- Resets the warehouse to the scenario: removes all purchase orders and stock
-- movements, removes items not in the scenario, sets stock and thresholds of the
-- scenario items and opens orders for their on_order. audit_log is kept.
create or replace function load_scenario(p_scenario_id text, p_audit jsonb)
returns jsonb
language plpgsql
as $$
declare
  v_count  integer;
  v_result jsonb;
begin
  if not exists (select 1 from scenarios where id = p_scenario_id) then
    perform _fail(404, 'unknown_scenario', format('Scenario %s does not exist', p_scenario_id));
  end if;

  delete from stock_movements;
  delete from purchase_orders;
  delete from items where sku not in (select sku from scenario_items where scenario_id = p_scenario_id);

  insert into items (sku, name, unit, location, quantity, min_qty, max_qty)
  select sku, name, unit, location, on_hand, reorder_threshold, target_level
  from scenario_items
  where scenario_id = p_scenario_id
  on conflict (sku) do update set
    name       = excluded.name,
    unit       = excluded.unit,
    location   = excluded.location,
    quantity   = excluded.quantity,
    min_qty    = excluded.min_qty,
    max_qty    = excluded.max_qty,
    updated_at = now();
  get diagnostics v_count = row_count;

  insert into stock_movements (item_id, type, quantity_delta, quantity_after, reason, actor, request_id)
  select i.id, 'adjust', s.on_hand, s.on_hand, format('Scenario %s', p_scenario_id),
         p_audit->>'actor', p_audit->>'request_id'
  from scenario_items s
  join items i on i.sku = s.sku
  where s.scenario_id = p_scenario_id and s.on_hand > 0;

  insert into purchase_orders (item_id, quantity, unit_price_amount, currency, marketplace_order_id,
                               merchant_id, created_by, request_id)
  select i.id, s.on_order, 0, 'PLN', format('scenario:%s', p_scenario_id), 'scenario',
         p_audit->>'actor', p_audit->>'request_id'
  from scenario_items s
  join items i on i.sku = s.sku
  where s.scenario_id = p_scenario_id and s.on_order > 0;

  v_result := jsonb_build_object('scenario_id', p_scenario_id, 'items_loaded', v_count);
  perform _audit_ok(p_audit, v_result);
  return v_result;
end;
$$;

--------------------------------------------------------------------------------
-- Access: only the backend (service_role key)
--------------------------------------------------------------------------------

alter table scenarios      enable row level security;
alter table scenario_items enable row level security;

revoke execute on function
  _fail(integer, text, text),
  _purchase_order_json(uuid),
  create_purchase_order(text, integer, numeric, text, text, text, text, text, jsonb),
  receive_purchase_order(uuid, integer, text, jsonb),
  cancel_purchase_order(uuid, text, jsonb),
  load_scenario(text, jsonb)
from public, anon, authenticated;

grant execute on function
  _fail(integer, text, text),
  _purchase_order_json(uuid),
  create_purchase_order(text, integer, numeric, text, text, text, text, text, jsonb),
  receive_purchase_order(uuid, integer, text, jsonb),
  cancel_purchase_order(uuid, text, jsonb),
  load_scenario(text, jsonb)
to service_role;

commit;
