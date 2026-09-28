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

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/health` | Health check |

## Docs

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- OpenAPI JSON: http://localhost:8000/openapi.json

## Supabase

Set in `.env`:

- `SUPABASE_URL` — project URL
- `SUPABASE_KEY` — anon or service-role key

Use `get_supabase_client()` from `app.db.supabase` in route handlers when you need the client.
