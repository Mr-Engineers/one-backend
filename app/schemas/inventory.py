"""Request / response models of the warehouse API (contract: Kontrakt API - Magazyn, 2026-10-03)."""
from datetime import datetime, timezone
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

PurchaseOrderStatus = Literal["open", "received", "cancelled"]
MovementType = Literal["receive", "consume", "adjust"]


################################################################################
# Money and supplier
################################################################################


class Money(BaseModel):
    """Contract: amount as a decimal string with 2 places (no floats), currency per ISO 4217."""

    model_config = ConfigDict(extra="forbid")

    amount: str = Field(..., pattern=r"^\d{1,10}\.\d{2}$", examples=["118.00"])
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
# Items
################################################################################


class Item(BaseModel):
    """A product with its current stock."""

    sku: str = Field(..., examples=["PAP-A4-80"])
    name: str = Field(..., examples=["Papier A4 80 g/m², karton 5 ryz"])
    unit: str = Field(..., examples=["karton"])
    category: str | None = Field(None, examples=["papier"])
    location: str = Field(..., examples=["Magazyn"])
    on_hand: int = Field(..., examples=[12])
    on_order: int = Field(..., examples=[0])
    reorder_threshold: int = Field(..., examples=[20])
    target_level: int = Field(..., examples=[50])
    is_low: bool = Field(..., description="on_hand + on_order < reorder_threshold", examples=[True])
    created_at: UtcDatetime
    updated_at: UtcDatetime


class ItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: Sku
    name: str = Field(..., min_length=1)
    unit: str = Field(..., min_length=1, examples=["szt"])
    category: str | None = None
    location: str = Field("Magazyn", min_length=1)
    reorder_threshold: int = Field(..., ge=0)
    target_level: int = Field(..., gt=0)
    initial_on_hand: int = Field(0, ge=0, description="Recorded as an 'Initial stock' adjust movement.")

    @model_validator(mode="after")
    def check_levels(self) -> "ItemCreate":
        if self.target_level < self.reorder_threshold:
            raise ValueError("target_level must be >= reorder_threshold")
        return self


class ItemUpdate(BaseModel):
    """Fields to change. on_hand cannot be changed here - use stock movements."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1)
    unit: str | None = Field(None, min_length=1)
    category: str | None = None
    location: str | None = Field(None, min_length=1)
    reorder_threshold: int | None = Field(None, ge=0)
    target_level: int | None = Field(None, gt=0)

    @model_validator(mode="after")
    def check_not_empty(self) -> "ItemUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to change")
        for field in ("name", "unit", "location", "reorder_threshold", "target_level"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


################################################################################
# Stock movements
################################################################################


class StockMovement(BaseModel):
    id: str
    sku: str
    type: MovementType
    quantity_delta: int = Field(..., description="+ receive, - consume, +/- adjust.")
    quantity_after: int = Field(..., description="on_hand after the movement.")
    reason: str | None = None
    purchase_order_id: str | None = None
    actor: str
    request_id: str | None = None
    created_at: UtcDatetime


class StockMovementCreate(BaseModel):
    """
    - consume: `quantity` taken from stock.
    - receive: `quantity` added without a purchase order (`reason` required).
    - adjust: `new_quantity` counted on the shelf (`reason` required).
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"sku": "PAP-A4-80", "type": "consume", "quantity": 2},
                {"sku": "PAP-A4-80", "type": "adjust", "new_quantity": 10, "reason": "Inwentaryzacja"},
            ]
        },
    )

    sku: Sku
    type: MovementType
    quantity: PositiveInt | None = None
    new_quantity: int | None = Field(None, ge=0)
    reason: str | None = None

    @model_validator(mode="after")
    def check_fields_for_type(self) -> "StockMovementCreate":
        if self.type == "adjust":
            if self.new_quantity is None or self.quantity is not None:
                raise ValueError("adjust takes new_quantity (not quantity)")
        elif self.quantity is None or self.new_quantity is not None:
            raise ValueError(f"{self.type} takes quantity (not new_quantity)")
        if self.type in ("adjust", "receive") and not (self.reason and self.reason.strip()):
            raise ValueError(f"{self.type} requires a reason")
        return self


class StockMovementResult(BaseModel):
    movement: StockMovement
    item: Item


################################################################################
# Purchase orders
################################################################################


class PurchaseOrderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: Sku
    quantity: PositiveInt = Field(..., examples=[38])
    unit_price: Money
    supplier: Supplier


class PurchaseOrder(BaseModel):
    id: str = Field(..., examples=["3b91c2d4-6f0e-4b8a-9d1e-2a7c5e8f1b30"])
    sku: str = Field(..., examples=["PAP-A4-80"])
    quantity: int = Field(..., examples=[38])
    quantity_received: int = Field(..., description="Received so far (partial deliveries).", examples=[0])
    status: PurchaseOrderStatus
    unit_price: Money
    supplier: Supplier
    created_by: str = Field(..., examples=["purchasing-agent"])
    created_at: UtcDatetime
    received_at: UtcDatetime | None = None
    cancelled_at: UtcDatetime | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "PurchaseOrder":
        """From a purchase_order_details row (flat database columns)."""
        return cls(
            id=str(row["id"]),
            sku=row["sku"],
            quantity=row["quantity"],
            quantity_received=row["quantity_received"],
            status=row["status"],
            unit_price=Money.from_db(row["unit_price_amount"], row["currency"]),
            supplier=Supplier(marketplace_order_id=row["marketplace_order_id"], merchant_id=row["merchant_id"]),
            created_by=row["created_by"],
            created_at=row["created_at"],
            received_at=row.get("received_at"),
            cancelled_at=row.get("cancelled_at"),
        )


class ReceiveRequest(BaseModel):
    """Omit the body (or `quantity`) to receive everything still outstanding."""

    model_config = ConfigDict(extra="forbid")

    quantity: PositiveInt | None = Field(None, description="Partial delivery.")


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, examples=["Marketplace cancelled the order"])


################################################################################
# Demo scenarios
################################################################################


class ScenarioItem(BaseModel):
    sku: str
    name: str
    unit: str
    on_hand: int
    on_order: int
    reorder_threshold: int
    target_level: int


class Scenario(BaseModel):
    id: str = Field(..., examples=["happy_path"])
    description: str
    items: list[ScenarioItem]


class ScenarioLoadResult(BaseModel):
    scenario_id: str = Field(..., examples=["happy_path"])
    items_loaded: int = Field(..., examples=[2])


################################################################################
# Audit log
################################################################################


class AuditEntry(BaseModel):
    id: int
    actor: str
    via_gateway: bool
    action: str
    request_id: str | None = None
    input: dict[str, Any]
    result: dict[str, Any] | None = None
    status: Literal["ok", "rejected", "error"]
    created_at: UtcDatetime
