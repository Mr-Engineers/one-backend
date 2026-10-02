from typing import Any

from pydantic import BaseModel, Field


class DbTestResponse(BaseModel):
    """Rows read from the Demo table, returned by GET /db-test."""

    table: str = Field(
        ...,
        description="Name of the queried table.",
        examples=["Demo"],
    )
    count: int = Field(
        ...,
        description="Number of returned rows.",
        examples=[2],
    )
    rows: list[dict[str, Any]] = Field(
        ...,
        description="Rows of the table as returned by Supabase.",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "table": "Demo",
                    "count": 1,
                    "rows": [{"id": 1, "created_at": "2026-10-02T18:00:00Z"}],
                }
            ]
        }
    }
