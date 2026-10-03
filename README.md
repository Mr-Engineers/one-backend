# one-backend

FastAPI backend boilerplate prepared for Supabase.

## Structure

```
app/
  api/routes/     # HTTP endpoints (versioned under API_PREFIX)
  core/           # Settings / config
  db/             # Supabase client
  schemas/        # Pydantic models (OpenAPI response shapes)
  main.py         # App factory + OpenAPI metadata
```

## Setup

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env   # then fill SUPABASE_URL / SUPABASE_KEY
```

## Run

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Endpoints

Office stockroom API used by the purchasing agent (through proxy-server) and the frontend.

| Method | Path | Agent tool | Description |
|--------|------|------------|-------------|
| GET | `/api/v1/health` | | Health check |
| GET | `/api/v1/items` | `get_stock()` | All items with stock, thresholds and `on_order` |
| GET | `/api/v1/items/{sku}` | `get_stock(sku)` | One item |
| GET | `/api/v1/low-stock` | `list_low_stock()` | Items below `min_qty` with `suggested_qty` |
| GET | `/api/v1/items/{sku}/movements` | `get_movements(sku)` | Stock history of an item |
| GET | `/api/v1/purchase-orders` | `list_purchase_orders()` | Orders, filter by `status` |
| GET | `/api/v1/purchase-orders/{id}` | `get_purchase_order(id)` | One order with lines |
| POST | `/api/v1/purchase-orders` | `create_purchase_order(...)` | Register an order placed in the shop (**gateway only**) |
| POST | `/api/v1/purchase-orders/{id}/receive` | `receive_purchase_order(...)` | Put a delivery on stock |
| POST | `/api/v1/purchase-orders/{id}/cancel` | | Cancel the undelivered rest (staff) |
| POST | `/api/v1/stock-movements` | | Consume / adjust / receive without an order (staff) |
| POST | `/api/v1/items` | | Add an item (admin) |
| PATCH | `/api/v1/items/{sku}` | | Change thresholds, location, shop mapping (admin) |
| GET | `/api/v1/audit-log` | | Every change and rejected attempt |

Which tools the agent may use is decided by proxy-server; the backend checks data
(stock never negative, no receiving more than ordered, ...).

### Gateway headers

| Header | Meaning |
|--------|---------|
| `X-Gateway-Token` | Shared secret with proxy-server (`GATEWAY_TOKEN`). Only with it `X-Actor` is trusted and purchase orders can be created. |
| `X-Actor` | e.g. `agent:purchasing`. Without a valid token the actor is recorded as `anonymous`. |
| `X-Request-Id` | Stored in the audit log and stock movements. |
| `Idempotency-Key` | Required for writes through the gateway; a retry returns the first result (HTTP 200) instead of doing the change twice. |

## Database

SQL lives in `supabase/`:

- `migrations/001_inventory_schema.sql` - tables and the `low_stock` view
- `migrations/002_inventory_functions.sql` - views, the functions every change goes through
  (change + stock movement + audit row in one transaction) and access rules (RLS)
- `seed.sql` - demo stockroom, 4 items below their minimum

Run them in this order in the Supabase SQL editor. After `002` the tables are only
reachable with the **service_role** key, so `SUPABASE_KEY` must be that key.

## Docs

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- OpenAPI JSON: http://localhost:8000/openapi.json

## Supabase

Set in `.env`:

- `SUPABASE_URL` — project URL
- `SUPABASE_KEY` — service-role key (the anon key has no access after migration `002`)
- `GATEWAY_TOKEN` — shared secret with proxy-server

Use `get_supabase_client()` from `app.db.supabase` in route handlers when you need the client.
