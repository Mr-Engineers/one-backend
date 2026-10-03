from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Sku = Annotated[str, Field(min_length=1, max_length=64, examples=["OFF-PAP-A4"])]
PositiveInt = Annotated[int, Field(gt=0)]

PurchaseOrderStatus = Literal["ordered", "partially_received", "received", "cancelled"]
MovementType = Literal["receive", "consume", "adjust"]


################################################################################
# Items
################################################################################


class Item(BaseModel):
    """A stockroom item with its current stock."""

    sku: str = Field(..., examples=["OFF-PAP-A4"])
    name: str = Field(..., examples=["Papier A4 500 ark."])
    category: str | None = Field(None, examples=["papier"])
    unit: str = Field(..., examples=["ream"])
    location: str = Field(..., examples=["Piętro 2 / Szafa B"])
    quantity: int = Field(..., description="Quantity on hand.", examples=[3])
    min_qty: int = Field(..., description="Below this the item is low on stock.", examples=[10])
    max_qty: int = Field(..., description="Target quantity after restocking.", examples=[40])
    shop_sku: str | None = Field(
        None,
        description="Product code in the shop (backend-2). Null: the agent cannot order this item.",
        examples=["SHOP-1042"],
    )
    on_order: int = Field(0, description="Ordered and not yet received.", examples=[0])
    is_low: bool = Field(..., description="quantity < min_qty", examples=[True])
    created_at: datetime
    updated_at: datetime


class ItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: Sku
    name: str = Field(..., min_length=1)
    category: str | None = None
    unit: str = "pcs"
    location: str = Field(..., min_length=1)
    min_qty: int = Field(..., ge=0)
    max_qty: int = Field(..., ge=0)
    shop_sku: str | None = None
    initial_quantity: int = Field(0, ge=0, description="Recorded as an 'Initial stock' adjust movement.")

    @model_validator(mode="after")
    def check_thresholds(self) -> "ItemCreate":
        if self.max_qty < self.min_qty:
            raise ValueError("max_qty must be >= min_qty")
        return self


class ItemUpdate(BaseModel):
    """Fields to change. quantity cannot be changed here - use stock movements."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1)
    category: str | None = None
    unit: str | None = Field(None, min_length=1)
    location: str | None = Field(None, min_length=1)
    min_qty: int | None = Field(None, ge=0)
    max_qty: int | None = Field(None, ge=0)
    shop_sku: str | None = None

    @model_validator(mode="after")
    def check_not_empty(self) -> "ItemUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to change")
        for field in ("name", "unit", "location", "min_qty", "max_qty"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class LowStockItem(BaseModel):
    """An item below its minimum, with how much to order."""

    sku: str = Field(..., examples=["OFF-PAP-A4"])
    name: str = Field(..., examples=["Papier A4 500 ark."])
    location: str = Field(..., examples=["Piętro 2 / Szafa B"])
    quantity: int = Field(..., examples=[3])
    min_qty: int = Field(..., examples=[10])
    max_qty: int = Field(..., examples=[40])
    on_order: int = Field(..., description="Ordered and not yet received.", examples=[0])
    suggested_qty: int = Field(
        ..., description="How much to order: max_qty - quantity - on_order.", examples=[37]
    )
    shop_sku: str | None = Field(None, examples=["SHOP-1042"])


################################################################################
# Stock movements
################################################################################


class StockMovement(BaseModel):
    id: UUID
    sku: str
    type: MovementType
    quantity_delta: int = Field(..., description="+ receive, - consume, +/- adjust.")
    quantity_after: int
    reason: str | None = None
    purchase_order_id: UUID | None = None
    actor: str
    request_id: str | None = None
    created_at: datetime


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
                {"sku": "OFF-PAP-A4", "type": "consume", "quantity": 2},
                {"sku": "OFF-PAP-A4", "type": "adjust", "new_quantity": 10, "reason": "Inwentaryzacja"},
                {"sku": "OFF-PAP-A4", "type": "receive", "quantity": 5, "reason": "Przeniesione z biura B"},
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


class PurchaseOrderLine(BaseModel):
    sku: str = Field(..., examples=["OFF-PAP-A4"])
    name: str = Field(..., examples=["Papier A4 500 ark."])
    shop_sku: str = Field(..., examples=["SHOP-1042"])
    quantity_ordered: int = Field(..., examples=[37])
    quantity_received: int = Field(..., examples=[0])
    unit_price: float | None = Field(None, examples=[24.99])


class PurchaseOrder(BaseModel):
    id: UUID
    status: PurchaseOrderStatus
    shop_order_id: str | None = Field(None, examples=["ORD-7781"])
    created_by: str = Field(..., examples=["agent:purchasing"])
    created_at: datetime
    received_at: datetime | None = None
    lines: list[PurchaseOrderLine]


class PurchaseOrderLineCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: Sku
    quantity: PositiveInt = Field(..., examples=[37])
    unit_price: float | None = Field(None, ge=0, examples=[24.99])


class PurchaseOrderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shop_order_id: str = Field(..., min_length=1, description="Order ID returned by the shop.", examples=["ORD-7781"])
    lines: list[PurchaseOrderLineCreate] = Field(..., min_length=1)


class ReceiveLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: Sku
    quantity: PositiveInt


class ReceiveRequest(BaseModel):
    """Omit `lines` to receive everything still outstanding."""

    model_config = ConfigDict(extra="forbid")

    lines: list[ReceiveLine] | None = Field(None, min_length=1)


class ReceivedLine(BaseModel):
    sku: str
    quantity: int
    quantity_after: int = Field(..., description="Quantity on hand after receiving.")


class ReceiveResult(BaseModel):
    purchase_order: PurchaseOrder
    received: list[ReceivedLine] = Field(..., description="Empty when the request was a replay.")


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, examples=["Sklep nie ma towaru"])


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
    created_at: datetime
