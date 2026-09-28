from datetime import datetime, timezone

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Health check payload returned by GET /health."""

    status: str = Field(
        ...,
        description="Service status indicator.",
        examples=["ok"],
    )
    service: str = Field(
        ...,
        description="Application service name.",
        examples=["one-backend"],
    )
    environment: str = Field(
        ...,
        description="Current runtime environment.",
        examples=["development"],
    )
    supabase_configured: bool = Field(
        ...,
        description="Whether Supabase credentials are present in the environment.",
    )
    timestamp: datetime = Field(
        ...,
        description="UTC timestamp when the health check was generated.",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "status": "ok",
                    "service": "one-backend",
                    "environment": "development",
                    "supabase_configured": True,
                    "timestamp": "2026-03-28T18:00:00Z",
                }
            ]
        }
    }


def build_health_response(
    *,
    service: str,
    environment: str,
    supabase_configured: bool,
) -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=service,
        environment=environment,
        supabase_configured=supabase_configured,
        timestamp=datetime.now(timezone.utc),
    )
