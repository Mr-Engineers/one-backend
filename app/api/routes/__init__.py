"""API route modules."""

from fastapi import APIRouter

from app.api.routes import audit_log, db_test, health, items, low_stock, purchase_orders, stock_movements

api_router = APIRouter()
api_router.include_router(health.router, tags=["Health"])
api_router.include_router(db_test.router, tags=["Database"])
api_router.include_router(items.router, tags=["Items"])
api_router.include_router(low_stock.router, tags=["Items"])
api_router.include_router(stock_movements.router, tags=["Stock movements"])
api_router.include_router(purchase_orders.router, tags=["Purchase orders"])
api_router.include_router(audit_log.router, tags=["Audit"])
