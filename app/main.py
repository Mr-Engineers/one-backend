from contextlib import asynccontextmanager
from typing import AsyncIterator

from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from postgrest.exceptions import APIError

from app.api.routes import api_router
from app.core.config import get_settings
from app.core.errors import error_body, register_error_handlers
from app.db.supabase import to_http_exception


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Place startup hooks here (e.g. warm Supabase client).
    yield
    # Place shutdown hooks here.


def create_app() -> FastAPI:
    settings = get_settings()

    application = FastAPI(
        title=settings.app_name,
        description=(
            "Warehouse API (contract: Kontrakt API - Magazyn). JSON in snake_case, times in ISO 8601 UTC, "
            'amounts as {"amount": "118.00", "currency": "PLN"}, errors as '
            '{"error": {"code": "...", "message": "..."}}. proxy-server authenticates with '
            "`Authorization: Bearer <token>` and sends `X-Request-Id` and `X-On-Behalf-Of`."
        ),
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        openapi_tags=[
            {
                "name": "Health",
                "description": "Liveness and readiness probes.",
            },
            {
                "name": "Database",
                "description": "Supabase connectivity checks.",
            },
            {
                "name": "Warehouse contract",
                "description": "Products to restock - the endpoint the purchasing agent uses.",
            },
            {
                "name": "Purchase orders",
                "description": "Orders placed in the marketplace and receiving their deliveries.",
            },
            {
                "name": "Merchants",
                "description": "Supplier profiles used by proxy-server to check orders.",
            },
            {
                "name": "Demo scenarios",
                "description": "Resetting the warehouse to a known state before a demo.",
            },
            {
                "name": "Items",
                "description": "Products and stock levels (web).",
            },
            {
                "name": "Stock movements",
                "description": "Manual stock changes: issue and adjustment (web).",
            },
        ],
    )

    register_error_handlers(application)

    @application.exception_handler(APIError)
    async def database_error_handler(_request: Request, exc: APIError) -> JSONResponse:
        api_exc = to_http_exception(exc)
        return JSONResponse(status_code=api_exc.status_code, content=error_body(api_exc.code, api_exc.detail))

    # Contract: OpenAPI under /openapi.json relative to the API base URL proxy-server uses
    # (http://backend:8000/api/v1); /openapi.json at the root stays for /docs.
    @application.get(f"{settings.api_prefix}/openapi.json", include_in_schema=False)
    def openapi_under_prefix() -> dict[str, Any]:
        return application.openapi()

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.include_router(api_router, prefix=settings.api_prefix)

    return application


app = create_app()
