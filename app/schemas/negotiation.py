from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


class NegotiationStateSchema(BaseModel):
    round_number: int = 0  # 0: Not started, 1: Hold price, 2: Add bundle, 3: Floor price offer, 4+: Cutoff/Pivot
    original_price: float | None = None
    floor_price: float | None = None
    
    customer_target_price: float | None = None
    last_offered_price: float | None = None
    offered_bundles: list[str] = Field(default_factory=list)  # ["matching gold stole", "khussa"]
    last_action: str | None = None  # persist across turns (e.g. ask_color_preference)
    
    is_floor_reached: bool = False
    is_closed: bool = False
    status: Literal["active", "accepted", "rejected_pivoted", "handover_required", "budget_too_low"] = "active"


class DiscountValidateRequestSchema(BaseModel):
    session_id: str
    product_id: str
    requested_price: float
    customer_phone: str | None = None


class DiscountValidateResponseSchema(BaseModel):
    approved: bool
    approved_price: float
    discount_percent: float
    discount_code: str | None = None
    code_expires_at: str | None = None
    message: str
