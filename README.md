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

Warehouse API per the contract *Kontrakt API - Magazyn* (2026-10-03). Base URL for
proxy-server: `http://backend:8000/api/v1`; the web reaches it as `<app>/api/...`.

| Method | Path | Consumer | Description |
|--------|------|----------|-------------|
| GET | `/low-stock` | agent (via proxy), web | Products with `on_hand + on_order < reorder_threshold`, with `qty_needed` |
| POST | `/purchase-orders` | agent (via proxy) **only** | Register an order placed in the marketplace (`Idempotency-Key` UUID required) |
| GET | `/merchants/{merchant_id}` | proxy | Supplier profile: domain age, country, verification, reputation |
| POST | `/purchase-orders/{id}/receive` | web / demo | Receive the whole delivery |
| POST | `/admin/scenarios/{scenario_id}/load` | demo | Reset the warehouse to a scenario (defined in `app/api/routes/admin.py`) |
| GET | `/admin/scenarios` | web / demo | Available scenarios |
| GET | `/purchase-orders`, `/purchase-orders/{id}` | web | Orders, filter by `status` and `sku` |
| POST | `/purchase-orders/{id}/cancel` | web | Cancel an open order |
| GET | `/items`, `/items/{sku}`, `/items/{sku}/movements` | web | Active products, stock and history |
| POST / PATCH | `/items`, `/items/{sku}` | web | Add a product, change thresholds |
| POST | `/stock-movements` | web | `issue` from stock or `adjustment` after a count |
| GET | `/health`, `/openapi.json` | | Health check, OpenAPI (also at the root `/openapi.json`) |

Conventions: snake_case, times in ISO 8601 UTC (`2026-10-03T14:05:00Z`), amounts as
`{"amount": "118.00", "currency": "PLN"}`, errors as `{"error": {"code": "...", "message": "..."}}`.

### Headers from proxy-server

| Header | Meaning |
|--------|---------|
| `Authorization: Bearer <GATEWAY_TOKEN>` | Token issued to proxy-server. Only with it `X-On-Behalf-Of` is trusted and purchase orders can be created. Any other bearer token (e.g. a Supabase session) is an anonymous caller. |
| `X-On-Behalf-Of` | Agent, e.g. `purchasing-agent`; recorded as the actor. |
| `X-Request-Id` | Stored with purchase orders and stock movements. |
| `Idempotency-Key` | UUID, required for `POST /purchase-orders`; the same key and body returns the same order (201). |

## Database

The API works on the `warehouse` schema (`products`, `stock_levels`, `suppliers`, `purchase_orders`,
`stock_movements`, views `stock_availability` / `low_stock`, function `receive_purchase_order`).
In Supabase add `warehouse` to *Settings → API → Exposed schemas* and grant the `service_role`
access to it, otherwise every request fails with `PGRST106`.

Only receiving a purchase order runs in one database transaction. Other changes are separate
requests; stock changes are guarded by the value read before them (409 `stock_changed` = retry).

The SQL in `supabase/` describes the previous `public` schema and is not used by the API.

## Docs

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- OpenAPI JSON: http://localhost:8000/openapi.json

## Supabase

Set in `.env`:

- `SUPABASE_URL` — project URL
- `SUPABASE_KEY` — service-role key
- `GATEWAY_TOKEN` — token issued to proxy-server (sent as `Authorization: Bearer`)

Use `get_supabase_client()` from `app.db.supabase` in route handlers when you need the client.
