-- Office stockroom: items, stock movements, purchase orders, audit log.
-- Already applied in Supabase; kept here so the database can be recreated.

create type movement_type as enum ('receive', 'consume', 'adjust');
create type po_status     as enum ('ordered', 'partially_received', 'received', 'cancelled');

create table items (
  id          uuid primary key default gen_random_uuid(),
  sku         text not null unique,            -- internal SKU, e.g. OFF-PAP-A4
  name        text not null,
  category    text,
  unit        text not null default 'pcs',
  location    text not null,                   -- e.g. 'Floor 2 / Cabinet B'
  quantity    integer not null default 0 check (quantity >= 0),
  min_qty     integer not null check (min_qty >= 0),
  max_qty     integer not null,
  shop_sku    text,                            -- product in the shop (backend-2); null = can't be restocked by the agent
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  check (max_qty >= min_qty)
);

create table purchase_orders (
  id              uuid primary key default gen_random_uuid(),
  status          po_status not null default 'ordered',
  shop_order_id   text,                        -- order ID returned by the shop
  idempotency_key text unique,
  created_by      text not null,               -- actor passed by the gateway
  created_at      timestamptz not null default now(),
  received_at     timestamptz
);

create table purchase_order_lines (
  id                uuid primary key default gen_random_uuid(),
  purchase_order_id uuid not null references purchase_orders(id) on delete cascade,
  item_id           uuid not null references items(id),
  shop_sku          text not null,
  quantity_ordered  integer not null check (quantity_ordered > 0),
  quantity_received integer not null default 0 check (quantity_received >= 0),
  unit_price        numeric(10,2),
  unique (purchase_order_id, item_id)
);

create table stock_movements (
  id                uuid primary key default gen_random_uuid(),
  item_id           uuid not null references items(id),
  type              movement_type not null,
  quantity_delta    integer not null check (quantity_delta <> 0),  -- + receive, - consume, +/- adjust
  quantity_after    integer not null,
  reason            text,
  purchase_order_id uuid references purchase_orders(id),
  actor             text not null,
  request_id        text,
  idempotency_key   text unique,
  created_at        timestamptz not null default now(),
  check (type <> 'adjust' or reason is not null)                   -- adjust always needs a reason
);

create table audit_log (
  id          bigint generated always as identity primary key,
  actor       text not null,                   -- 'agent:purchasing', 'user:anna'
  via_gateway boolean not null,
  action      text not null,                   -- 'purchase_order.create', 'stock.consume'
  request_id  text,
  input       jsonb not null,
  result      jsonb,
  status      text not null,                   -- ok / rejected / error
  created_at  timestamptz not null default now()
);

-- Low stock, accounting for what is already ordered and not yet delivered
create view low_stock as
select i.id, i.sku, i.name, i.location, i.quantity, i.min_qty, i.max_qty, i.shop_sku,
       coalesce(o.on_order, 0)                                           as on_order,
       greatest(i.max_qty - i.quantity - coalesce(o.on_order, 0), 0)     as suggested_qty
from items i
left join (
  select l.item_id, sum(l.quantity_ordered - l.quantity_received) as on_order
  from purchase_order_lines l
  join purchase_orders p on p.id = l.purchase_order_id
  where p.status in ('ordered', 'partially_received')
  group by l.item_id
) o on o.item_id = i.id
where i.quantity < i.min_qty;
