"""API route modules."""

from fastapi import APIRouter

from app.api.routes import db_test, health

api_router = APIRouter()
api_router.include_router(health.router, tags=["Health"])
api_router.include_router(db_test.router, tags=["Database"])
