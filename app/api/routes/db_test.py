from fastapi import APIRouter, HTTPException, status

from app.core.config import get_settings
from app.db.supabase import get_supabase_client
from app.schemas.db_test import DbTestResponse

DEMO_TABLE = "products"
MAX_ROWS = 100

router = APIRouter()


@router.get(
    "/db-test",
    response_model=DbTestResponse,
    status_code=status.HTTP_200_OK,
    summary="Database test",
    description=(
        f"Reads up to {MAX_ROWS} rows from the `{DEMO_TABLE}` table in Supabase "
        "to verify the database connection."
    ),
    responses={
        502: {"description": "Supabase query failed."},
        503: {"description": "Supabase credentials are not configured."},
    },
)
def db_test() -> DbTestResponse:
    if not get_settings().supabase_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase is not configured. Set SUPABASE_URL and SUPABASE_KEY.",
        )

    try:
        result = (
            get_supabase_client().table(DEMO_TABLE).select("*").limit(MAX_ROWS).execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Supabase query failed: {exc}",
        ) from exc

    return DbTestResponse(table=DEMO_TABLE, count=len(result.data), rows=result.data)
