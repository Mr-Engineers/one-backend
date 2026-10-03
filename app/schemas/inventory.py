"""Request / response models of the warehouse API (database schema: warehouse)."""
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, model_validator


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# Contract: ISO 8601 in UTC, e.g. 2026-10-03T14:05:00Z
UtcDatetime = Annotated[
    datetime,
    PlainSerializer(_utc_iso, return_type=str, when_used="json-unless-none"),
    Field(examples=["2026-10-03T14:05:00Z"]),
]
Sku = Annotated[str, Field(min_length=1, max_length=64, examples=["PAP-A4-80"])]
PositiveInt = Annotated[int, Field(gt=0)]

# warehouse.po_status
PurchaseOrderStatus = Literal["open", "received", "cancelled"]
# warehouse.movement_type
MovementType = Literal["receipt", "issue", "adjustment", "scenario_reset"]
# Movements recorded by hand; receipt comes from receiving a purchase order, scenario_reset from loading a scenario
ManualMovementType = Literal["issue", "adjustment"]


################################################################################
# Money and supplier
################################################################################


class Money(BaseModel):
    """Contract: amount as a decimal string with 2 places (no floats), currency per ISO 4217."""

    model_config = ConfigDict(extra="forbid")

    amount: str = Field(..., pattern=r"^\d{1,12}\.\d{2}$", examples=["118.00"])
    currency: str = Field(..., pattern=r"^[A-Z]{3}$", examples=["PLN"])

    @classmethod
    def from_db(cls, amount: Any, currency: str) -> "Money":
        return cls(amount=str(Decimal(str(amount)).quantize(Decimal("0.01"))), currency=currency.strip())


class Supplier(BaseModel):
    model_config = ConfigDict(extra="forbid")

    marketplace_order_id: str = Field(..., min_length=1, examples=["ord_8f2c"])
    merchant_id: str = Field(..., min_length=1, examples=["mer_biuromax"])


################################################################################
# Low stock
################################################################################


class LowStockItem(BaseModel):
    sku: str = Field(..., description="Product ID, shared with the marketplace.", examples=["PAP-A4-80"])
    name: str = Field(..., examples=["Papier A4 80 g/m², karton 5 ryz"])
    unit: str = Field(..., examples=["karton"])
    on_hand: int = Field(..., description="Physical stock.", examples=[12])
    on_order: int = Field(..., description="Sum of open purchase orders.", examples=[0])
    reorder_threshold: int = Field(..., examples=[20])
    target_level: int = Field(..., description="Target stock after restocking.", examples=[50])
    qty_needed: int = Field(..., description="max(target_level - on_hand - on_order, 0)", examples=[38])


class LowStockResponse(BaseModel):
    items: list[LowStockItem] = Field(..., description="Empty list = nothing to order.")
    generated_at: UtcDatetime


################################################################################
# Items (warehouse.products + warehouse.stock_availability)
################################################################################


class Item(BaseModel):
    """An active product with its current stock."""

    sku: str = Field(..., examples=["PAP-A4-80"])
    name: str = Field(..., examples=["Papier A4 80 g/m², karton 5 ryz"])
    unit: str = Field(..., examples=["karton"])
    on_hand: int = Field(..., examples=[12])
    on_order: int = Field(..., description="Sum of open purchase orders.", examples=[0])
    reorder_threshold: int = Field(..., examples=[20])
    target_level: int = Field(..., examples=[50])
    qty_needed: int = Field(..., description="max(target_level - on_hand - on_order, 0)", examples=[38])
    max_order_qty: int | None = Field(None, description="Optional ceiling for a single order.", examples=[100])
    is_low: bool = Field(..., description="on_hand + on_order < reorder_threshold", examples=[True])
    last_order_at: UtcDatetime | None = Field(None, description="Newest open purchase order.")
    created_at: UtcDatetime
    updated_at: UtcDatetime

    @classmethod
    def from_rows(cls, stock: dict[str, Any], product: dict[str, Any]) -> "Item":
        """From a stock_availability row and the matching products row."""
        return cls(
            **{key: stock[key] for key in (
                "sku", "name", "unit", "on_hand", "on_order", "reorder_threshold", "target_level", "qty_needed",
            )},
            max_order_qty=product.get("max_order_qty"),
            is_low=stock["on_hand"] + stock["on_order"] < stock["reorder_threshold"],
            last_order_at=stock.get("last_order_at"),
            created_at=product["created_at"],
            updated_at=product["updated_at"],
        )


def _check_levels(reorder_threshold: int | None, target_level: int | None) -> None:
    if reorder_threshold is not None and target_level is not None and target_level < reorder_threshold:
        raise ValueError("target_level must be >= reorder_threshold")


class ItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: Sku
    name: str = Field(..., min_length=1)
    unit: str = Field(..., min_length=1, examples=["szt"])
    reorder_threshold: int = Field(..., ge=0)
    target_level: int = Field(..., gt=0)
    max_order_qty: PositiveInt | None = None
    initial_on_hand: int = Field(0, ge=0, description="Recorded as an 'Initial stock' adjustment movement.")

    @model_validator(mode="after")
    def check_levels(self) -> "ItemCreate":
        _check_levels(self.reorder_threshold, self.target_level)
        return self


class ItemUpdate(BaseModel):
    """Fields to change. on_hand cannot be changed here - use stock movements."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1)
    unit: str | None = Field(None, min_length=1)
    reorder_threshold: int | None = Field(None, ge=0)
    target_level: int | None = Field(None, gt=0)
    max_order_qty: PositiveInt | None = Field(None, description="null removes the ceiling.")

    @model_validator(mode="after")
    def check_not_empty(self) -> "ItemUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to change")
        for field in ("name", "unit", "reorder_threshold", "target_level"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        _check_levels(self.reorder_threshold, self.target_level)
        return self


################################################################################
# Stock movements (warehouse.stock_movements)
################################################################################


class StockMovement(BaseModel):
    id: int
    sku: str
    movement_type: MovementType
    delta: int = Field(..., description="+ receipt, - issue, +/- adjustment, any for scenario_reset.")
    on_hand_after: int = Field(..., description="on_hand after the movement.")
    purchase_order_id: str | None = None
    reason: str | None = None
    actor: str
    request_id: str | None = None
    created_at: UtcDatetime


class StockMovementCreate(BaseModel):
    """
    - issue: `quantity` taken from stock (`reason` optional).
    - adjustment: `new_quantity` counted on the shelf (`reason` required).

    Deliveries are recorded by receiving a purchase order (`receipt`).
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"sku": "PAP-A4-80", "movement_type": "issue", "quantity": 2},
                {"sku": "PAP-A4-80", "movement_type": "adjustment", "new_quantity": 10, "reason": "Inwentaryzacja"},
            ]
        },
    )

    sku: Sku
    movement_type: ManualMovementType
    quantity: PositiveInt | None = None
    new_quantity: int | None = Field(None, ge=0)
    reason: str | None = None

    @model_validator(mode="after")
    def check_fields_for_type(self) -> "StockMovementCreate":
        if self.movement_type == "adjustment":
            if self.new_quantity is None or self.quantity is not None:
                raise ValueError("adjustment takes new_quantity (not quantity)")
            if not (self.reason and self.reason.strip()):
                raise ValueError("adjustment requires a reason")
        elif self.quantity is None or self.new_quantity is not None:
            raise ValueError("issue takes quantity (not new_quantity)")
        return self


class StockMovementResult(BaseModel):
    movement: StockMovement
    item: Item


################################################################################
# Purchase orders (warehouse.purchase_orders)
################################################################################


class PurchaseOrderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: Sku
    quantity: PositiveInt = Field(..., examples=[38])
    unit_price: Money
    supplier: Supplier


class PurchaseOrder(BaseModel):
    id: str = Field(..., examples=["po_3b91c2d4"])
    sku: str = Field(..., examples=["PAP-A4-80"])
    quantity: int = Field(..., examples=[38])
    status: PurchaseOrderStatus
    unit_price: Money
    total: Money = Field(..., description="unit_price x quantity, in the order currency.")
    total_eur: Money | None = Field(None, description="Total in EUR for rules and budgets; null when not converted.")
    supplier: Supplier
    on_behalf_of: str | None = Field(None, description="Agent that placed the order (X-On-Behalf-Of).", examples=["purchasing-agent"])
    request_id: str | None = Field(None, description="X-Request-Id of the request that created the order.")
    created_at: UtcDatetime
    received_at: UtcDatetime | None = None
    cancelled_at: UtcDatetime | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "PurchaseOrder":
        """From a warehouse.purchase_orders row."""
        return cls(
            id=row["id"],
            sku=row["sku"],
            quantity=row["quantity"],
            status=row["status"],
            unit_price=Money.from_db(row["unit_price"], row["currency"]),
            total=Money.from_db(row["total"], row["currency"]),
            total_eur=Money.from_db(row["total_eur"], "EUR") if row.get("total_eur") is not None else None,
            supplier=Supplier(marketplace_order_id=row["marketplace_order_id"], merchant_id=row["merchant_id"]),
            on_behalf_of=row.get("on_behalf_of"),
            request_id=row.get("request_id"),
            created_at=row["created_at"],
            received_at=row.get("received_at"),
            cancelled_at=row.get("cancelled_at"),
        )


################################################################################
# Merchants (warehouse.suppliers)
################################################################################


class Merchant(BaseModel):
    """Supplier profile, used by proxy-server to enrich and check purchase orders."""

    merchant_id: str = Field(..., examples=["mer_biuromax"])
    name: str = Field(..., examples=["BiuroMax"])
    domain: str = Field(..., examples=["biuromax.pl"])
    country: str = Field(..., description="ISO 3166-1 alpha-2.", examples=["PL"])
    domain_registered_at: date = Field(..., examples=["2014-03-11"])
    verified: bool
    reputation_score: float | None = Field(None, ge=0, le=1, description="null = no reviews yet.", examples=[0.92])
    reviews_count: int = Field(..., examples=[1840])
    created_at: UtcDatetime


################################################################################
# Demo scenarios
################################################################################


class ScenarioItem(BaseModel):
    sku: str
    name: str
    unit: str
    on_hand: int
    reorder_threshold: int
    target_level: int


class Scenario(BaseModel):
    id: str = Field(..., examples=["happy_path"])
    description: str
    items: list[ScenarioItem]


class ScenarioLoadResult(BaseModel):
    scenario_id: str = Field(..., examples=["happy_path"])
    items_loaded: int = Field(..., examples=[2])
    purchase_orders_cancelled: int = Field(..., description="Open orders cancelled so on_order starts at 0.", examples=[0])
    products_deactivated: int = Field(..., description="Products outside the scenario, hidden from the API.", examples=[0])
