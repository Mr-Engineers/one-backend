from fastapi import APIRouter, status

from app.core.config import get_settings
from app.schemas.health import HealthResponse, build_health_response

router = APIRouter()


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Health check",
    description=(
        "Returns service liveness information and whether Supabase "
        "credentials are configured. Does not open a database connection."
    ),
    responses={
        200: {
            "description": "Service is healthy.",
            "model": HealthResponse,
        },
    },
)
def health_check() -> HealthResponse:
    settings = get_settings()
    return build_health_response(
        service=settings.app_name,
        environment=settings.app_env,
        supabase_configured=settings.supabase_configured,
    )
