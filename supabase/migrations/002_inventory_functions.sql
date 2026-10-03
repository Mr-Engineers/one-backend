-- Views and functions used by the backend API.
--
-- Every change to stock or purchase orders goes through a function below, so the
-- change, its stock movement and its audit_log row are written in one transaction.
--
-- Errors use SQLSTATE 'PTxxx': PostgREST answers them with HTTP status xxx and the
-- backend passes that status on (PT404 -> 404, PT409 -> 409, PT422 -> 422).
--
-- p_audit (built by the backend from the gateway headers):
--   {"actor": "...", "via_gateway": true, "request_id": "...", "action": "...", "input": {...}}

--------------------------------------------------------------------------------
-- Views
--------------------------------------------------------------------------------

-- Every item with what is on order and whether it is below its minimum
create or replace view item_stock as
select i.*,
       coalesce(o.on_order, 0)::integer as on_order,
       i.quantity < i.min_qty           as is_low
from items i
left join (
  select l.item_id, sum(l.quantity_ordered - l.quantity_received) as on_order
  from purchase_order_lines l
  join purchase_orders p on p.id = l.purchase_order_id
  where p.status in ('ordered', 'partially_received')
  group by l.item_id
) o on o.item_id = i.id;

-- Purchase orders with their lines as one JSON column
create or replace view purchase_order_details as
select p.*,
       coalesce((
         select jsonb_agg(jsonb_build_object(
                  'sku',               i.sku,
                  'name',              i.name,
                  'shop_sku',          l.shop_sku,
                  'quantity_ordered',  l.quantity_ordered,
                  'quantity_received', l.quantity_received,
                  'unit_price',        l.unit_price
                ) order by i.sku)
         from purchase_order_lines l
         join items i on i.id = l.item_id
         where l.purchase_order_id = p.id
       ), '[]'::jsonb) as lines
from purchase_orders p;

-- Stock movements with the item SKU (the API identifies items by SKU)
create or replace view stock_movement_details as
select m.*, i.sku
from stock_movements m
join items i on i.id = m.item_id;

--------------------------------------------------------------------------------
-- Helpers
--------------------------------------------------------------------------------

create or replace function _audit_ok(p_audit jsonb, p_result jsonb)
returns void
language sql
as $$
  insert into audit_log (actor, via_gateway, action, request_id, input, result, status)
  values (
    p_audit->>'actor',
    coalesce((p_audit->>'via_gateway')::boolean, false),
    p_audit->>'action',
    p_audit->>'request_id',
    coalesce(p_audit->'input', '{}'::jsonb),
    p_result,
    'ok'
  );
$$;

create or replace function _purchase_order_json(p_id uuid)
returns jsonb
language sql
stable
as $$
  select to_jsonb(d) from purchase_order_details d where d.id = p_id;
$$;

--------------------------------------------------------------------------------
-- Stock movements: consume / adjust / receive without a purchase order
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
  -- Replay of an already processed request: return the original result
  if p_idempotency_key is not null then
    select * into v_movement from stock_movements where idempotency_key = p_idempotency_key;
    if found then
      select * into v_item from items where id = v_movement.item_id;
      if v_item.sku <> p_sku or v_movement.type <> p_type then
        raise exception using errcode = 'PT409',
          message = 'Idempotency-Key was already used for a different request';
      end if;
      return jsonb_build_object('movement', to_jsonb(v_movement) || jsonb_build_object('sku', v_item.sku),
                                'item', to_jsonb(v_item), 'replayed', true);
    end if;
  end if;

  select * into v_item from items where sku = p_sku for update;
  if not found then
    raise exception using errcode = 'PT404', message = format('Item %s not found', p_sku);
  end if;

  if p_type = 'adjust' then
    if p_new_quantity is null or p_new_quantity < 0 then
      raise exception using errcode = 'PT422', message = 'adjust requires new_quantity >= 0';
    end if;
    v_delta := p_new_quantity - v_item.quantity;
    if v_delta = 0 then
      raise exception using errcode = 'PT422',
        message = format('Item %s already has quantity %s, nothing to adjust', p_sku, v_item.quantity);
    end if;
  else
    if p_quantity is null or p_quantity <= 0 then
      raise exception using errcode = 'PT422', message = format('%s requires quantity > 0', p_type);
    end if;
    v_delta := case when p_type = 'consume' then -p_quantity else p_quantity end;
  end if;

  if p_type in ('adjust', 'receive') and v_reason is null then
    raise exception using errcode = 'PT422', message = format('%s requires a reason', p_type);
  end if;

  if v_item.quantity + v_delta < 0 then
    raise exception using errcode = 'PT409',
      message = format('Not enough stock of %s: %s on hand, %s requested', p_sku, v_item.quantity, -v_delta);
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
                                 'item', to_jsonb(v_item), 'replayed', false);
  perform _audit_ok(p_audit, v_result);
  return v_result;
end;
$$;

--------------------------------------------------------------------------------
-- Purchase orders
--------------------------------------------------------------------------------

-- Registers an order already placed in the shop. Does not change quantity.
-- p_lines: [{"sku": "...", "quantity": 1, "unit_price": 9.99}, ...]
create or replace function create_purchase_order(
  p_shop_order_id   text,
  p_lines           jsonb,
  p_idempotency_key text,
  p_audit           jsonb
)
returns jsonb
language plpgsql
as $$
declare
  v_po     purchase_orders%rowtype;
  v_item   items%rowtype;
  v_line   jsonb;
  v_result jsonb;
begin
  if p_idempotency_key is not null then
    select * into v_po from purchase_orders where idempotency_key = p_idempotency_key;
    if found then
      if v_po.shop_order_id is distinct from p_shop_order_id then
        raise exception using errcode = 'PT409',
          message = 'Idempotency-Key was already used for a different request';
      end if;
      return jsonb_build_object('purchase_order', _purchase_order_json(v_po.id), 'replayed', true);
    end if;
  end if;

  if nullif(trim(p_shop_order_id), '') is null then
    raise exception using errcode = 'PT422', message = 'shop_order_id is required';
  end if;

  if p_lines is null or jsonb_typeof(p_lines) <> 'array' or jsonb_array_length(p_lines) = 0 then
    raise exception using errcode = 'PT422', message = 'At least one line is required';
  end if;

  if (select count(distinct e->>'sku') from jsonb_array_elements(p_lines) e) <> jsonb_array_length(p_lines) then
    raise exception using errcode = 'PT422', message = 'Each SKU may appear only once per order';
  end if;

  insert into purchase_orders (status, shop_order_id, idempotency_key, created_by)
  values ('ordered', p_shop_order_id, p_idempotency_key, p_audit->>'actor')
  returning * into v_po;

  for v_line in select * from jsonb_array_elements(p_lines) loop
    select * into v_item from items where sku = v_line->>'sku';
    if not found then
      raise exception using errcode = 'PT404', message = format('Item %s not found', v_line->>'sku');
    end if;
    if v_item.shop_sku is null then
      raise exception using errcode = 'PT422',
        message = format('Item %s has no shop_sku, it cannot be ordered from the shop', v_item.sku);
    end if;
    if coalesce((v_line->>'quantity')::integer, 0) <= 0 then
      raise exception using errcode = 'PT422', message = format('Quantity of %s must be > 0', v_item.sku);
    end if;

    insert into purchase_order_lines (purchase_order_id, item_id, shop_sku, quantity_ordered, unit_price)
    values (v_po.id, v_item.id, v_item.shop_sku, (v_line->>'quantity')::integer, (v_line->>'unit_price')::numeric);
  end loop;

  v_result := jsonb_build_object('purchase_order', _purchase_order_json(v_po.id), 'replayed', false);
  perform _audit_ok(p_audit, v_result);
  return v_result;
end;
$$;

-- Puts delivered goods on stock.
-- p_lines null: receive everything still outstanding.
-- p_lines [{"sku": "...", "quantity": 1}, ...]: partial delivery.
create or replace function receive_purchase_order(
  p_purchase_order_id uuid,
  p_lines             jsonb,
  p_idempotency_key   text,
  p_audit             jsonb
)
returns jsonb
language plpgsql
as $$
declare
  v_po       purchase_orders%rowtype;
  v_item     items%rowtype;
  v_line     record;
  v_qty      integer;
  v_received jsonb := '[]'::jsonb;
  v_result   jsonb;
  v_unknown  text;
begin
  -- One movement per line, keyed '<Idempotency-Key>:<sku>'
  if p_idempotency_key is not null
     and exists (select 1 from stock_movements where idempotency_key like p_idempotency_key || ':%') then
    if not exists (select 1 from stock_movements
                   where idempotency_key like p_idempotency_key || ':%'
                     and purchase_order_id = p_purchase_order_id) then
      raise exception using errcode = 'PT409',
        message = 'Idempotency-Key was already used for a different request';
    end if;
    return jsonb_build_object('purchase_order', _purchase_order_json(p_purchase_order_id),
                              'received', '[]'::jsonb, 'replayed', true);
  end if;

  select * into v_po from purchase_orders where id = p_purchase_order_id for update;
  if not found then
    raise exception using errcode = 'PT404', message = format('Purchase order %s not found', p_purchase_order_id);
  end if;
  if v_po.status in ('received', 'cancelled') then
    raise exception using errcode = 'PT409', message = format('Purchase order is already %s', v_po.status);
  end if;

  if p_lines is not null then
    if jsonb_typeof(p_lines) <> 'array' or jsonb_array_length(p_lines) = 0 then
      raise exception using errcode = 'PT422', message = 'lines must be a non-empty list or omitted';
    end if;
    if (select count(distinct e->>'sku') from jsonb_array_elements(p_lines) e) <> jsonb_array_length(p_lines) then
      raise exception using errcode = 'PT422', message = 'Each SKU may appear only once';
    end if;

    select e->>'sku' into v_unknown
    from jsonb_array_elements(p_lines) e
    where not exists (
      select 1 from purchase_order_lines l join items i on i.id = l.item_id
      where l.purchase_order_id = p_purchase_order_id and i.sku = e->>'sku'
    )
    limit 1;
    if v_unknown is not null then
      raise exception using errcode = 'PT422', message = format('Item %s is not part of this purchase order', v_unknown);
    end if;
  end if;

  for v_line in
    select l.id as line_id, l.item_id, i.sku,
           l.quantity_ordered - l.quantity_received as outstanding,
           (r.e->>'quantity')::integer              as requested
    from purchase_order_lines l
    join items i on i.id = l.item_id
    left join jsonb_array_elements(coalesce(p_lines, '[]'::jsonb)) r(e) on r.e->>'sku' = i.sku
    where l.purchase_order_id = p_purchase_order_id
    order by i.sku
  loop
    v_qty := case when p_lines is null then v_line.outstanding else coalesce(v_line.requested, 0) end;
    continue when v_qty = 0;

    if v_qty < 0 or v_qty > v_line.outstanding then
      raise exception using errcode = 'PT422',
        message = format('Cannot receive %s of %s: %s outstanding', v_qty, v_line.sku, v_line.outstanding);
    end if;

    update items
    set quantity = quantity + v_qty, updated_at = now()
    where id = v_line.item_id
    returning * into v_item;

    insert into stock_movements (item_id, type, quantity_delta, quantity_after, reason,
                                 purchase_order_id, actor, request_id, idempotency_key)
    values (v_item.id, 'receive', v_qty, v_item.quantity,
            format('Purchase order %s', coalesce(v_po.shop_order_id, v_po.id::text)),
            v_po.id, p_audit->>'actor', p_audit->>'request_id',
            case when p_idempotency_key is null then null else p_idempotency_key || ':' || v_item.sku end);

    update purchase_order_lines
    set quantity_received = quantity_received + v_qty
    where id = v_line.line_id;

    v_received := v_received || jsonb_build_object('sku', v_item.sku, 'quantity', v_qty,
                                                   'quantity_after', v_item.quantity);
  end loop;

  if jsonb_array_length(v_received) = 0 then
    raise exception using errcode = 'PT422', message = 'Nothing to receive';
  end if;

  update purchase_orders
  set status = case
                 when exists (select 1 from purchase_order_lines
                              where purchase_order_id = v_po.id and quantity_received < quantity_ordered)
                 then 'partially_received'::po_status
                 else 'received'::po_status
               end,
      received_at = case
                      when exists (select 1 from purchase_order_lines
                                   where purchase_order_id = v_po.id and quantity_received < quantity_ordered)
                      then null
                      else now()
                    end
  where id = v_po.id;

  v_result := jsonb_build_object('purchase_order', _purchase_order_json(v_po.id),
                                 'received', v_received, 'replayed', false);
  perform _audit_ok(p_audit, v_result);
  return v_result;
end;
$$;

-- Cancels the not yet delivered rest of an order (already received stock stays).
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
    raise exception using errcode = 'PT422', message = 'reason is required';
  end if;

  select * into v_po from purchase_orders where id = p_purchase_order_id for update;
  if not found then
    raise exception using errcode = 'PT404', message = format('Purchase order %s not found', p_purchase_order_id);
  end if;
  if v_po.status in ('received', 'cancelled') then
    raise exception using errcode = 'PT409', message = format('Purchase order is already %s', v_po.status);
  end if;

  update purchase_orders set status = 'cancelled' where id = v_po.id;

  v_result := jsonb_build_object('purchase_order', _purchase_order_json(v_po.id));
  perform _audit_ok(p_audit, v_result);
  return v_result;
end;
$$;

--------------------------------------------------------------------------------
-- Item management
--------------------------------------------------------------------------------

-- p_item: {"sku", "name", "category", "unit", "location", "min_qty", "max_qty", "shop_sku"}
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
    raise exception using errcode = 'PT409', message = format('Item %s already exists', p_item->>'sku');
  end if;

  insert into items (sku, name, category, unit, location, min_qty, max_qty, shop_sku)
  values (
    p_item->>'sku',
    p_item->>'name',
    p_item->>'category',
    coalesce(p_item->>'unit', 'pcs'),
    p_item->>'location',
    (p_item->>'min_qty')::integer,
    (p_item->>'max_qty')::integer,
    p_item->>'shop_sku'
  )
  returning * into v_item;

  -- The starting quantity is recorded as a movement, so history matches the stock
  if coalesce(p_initial_quantity, 0) > 0 then
    update items set quantity = p_initial_quantity where id = v_item.id returning * into v_item;
    insert into stock_movements (item_id, type, quantity_delta, quantity_after, reason, actor, request_id)
    values (v_item.id, 'adjust', p_initial_quantity, p_initial_quantity, 'Initial stock',
            p_audit->>'actor', p_audit->>'request_id');
  end if;

  v_result := jsonb_build_object('item', to_jsonb(v_item));
  perform _audit_ok(p_audit, v_result);
  return v_result;
end;
$$;

-- p_changes: only the fields to change (name, category, unit, location, min_qty, max_qty, shop_sku).
-- quantity is never changed here - use stock movements.
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
    name       = case when p_changes ? 'name'     then p_changes->>'name'                else name     end,
    category   = case when p_changes ? 'category' then p_changes->>'category'            else category end,
    unit       = case when p_changes ? 'unit'     then p_changes->>'unit'                else unit     end,
    location   = case when p_changes ? 'location' then p_changes->>'location'            else location end,
    min_qty    = case when p_changes ? 'min_qty'  then (p_changes->>'min_qty')::integer  else min_qty  end,
    max_qty    = case when p_changes ? 'max_qty'  then (p_changes->>'max_qty')::integer  else max_qty  end,
    shop_sku   = case when p_changes ? 'shop_sku' then p_changes->>'shop_sku'            else shop_sku end,
    updated_at = now()
  where sku = p_sku
  returning * into v_item;

  if not found then
    raise exception using errcode = 'PT404', message = format('Item %s not found', p_sku);
  end if;

  v_result := jsonb_build_object('item', to_jsonb(v_item));
  perform _audit_ok(p_audit, v_result);
  return v_result;
end;
$$;

--------------------------------------------------------------------------------
-- Access: only the backend (service_role key) may read or change the data.
--
-- RLS without policies denies the anon / authenticated keys; service_role bypasses
-- RLS. Views run as their owner by default (which skips RLS), so they are switched
-- to security_invoker.
--------------------------------------------------------------------------------

alter table items                enable row level security;
alter table stock_movements      enable row level security;
alter table purchase_orders      enable row level security;
alter table purchase_order_lines enable row level security;
alter table audit_log            enable row level security;

alter view low_stock              set (security_invoker = true);
alter view item_stock             set (security_invoker = true);
alter view purchase_order_details set (security_invoker = true);
alter view stock_movement_details set (security_invoker = true);

--------------------------------------------------------------------------------
-- Functions: only the backend (service_role key) may call them.
-- Functions are executable by PUBLIC by default, which would let anyone with
-- the anon key change stock through the Supabase REST API, bypassing the gateway.
--------------------------------------------------------------------------------

revoke execute on function
  _audit_ok(jsonb, jsonb),
  _purchase_order_json(uuid),
  record_movement(text, movement_type, integer, integer, text, text, jsonb),
  create_purchase_order(text, jsonb, text, jsonb),
  receive_purchase_order(uuid, jsonb, text, jsonb),
  cancel_purchase_order(uuid, text, jsonb),
  create_item(jsonb, integer, jsonb),
  update_item(text, jsonb, jsonb)
from public, anon, authenticated;

grant execute on function
  _audit_ok(jsonb, jsonb),
  _purchase_order_json(uuid),
  record_movement(text, movement_type, integer, integer, text, text, jsonb),
  create_purchase_order(text, jsonb, text, jsonb),
  receive_purchase_order(uuid, jsonb, text, jsonb),
  cancel_purchase_order(uuid, text, jsonb),
  create_item(jsonb, integer, jsonb),
  update_item(text, jsonb, jsonb)
to service_role;
