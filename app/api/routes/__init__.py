"""API route modules."""

from fastapi import APIRouter

from app.api.routes import admin, db_test, health, items, low_stock, merchants, purchase_orders, stock_movements

api_router = APIRouter()
api_router.include_router(health.router, tags=["Health"])
api_router.include_router(db_test.router, tags=["Database"])
api_router.include_router(low_stock.router, tags=["Warehouse contract"])
api_router.include_router(purchase_orders.router, tags=["Purchase orders"])
api_router.include_router(merchants.router, tags=["Merchants"])
api_router.include_router(admin.router, tags=["Demo scenarios"])
api_router.include_router(items.router, tags=["Items"])
api_router.include_router(stock_movements.router, tags=["Stock movements"])
