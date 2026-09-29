from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


class SizeReservationRequestSchema(BaseModel):
    session_id: str
    product_id: str
    size: str
    color: str = "IVORY"
    quantity: int = 1
    customer_name: str
    customer_phone: str
    hold_hours: int = 24


class SizeReservationResponseSchema(BaseModel):
    reservation_id: str
    expires_at: str
    status: Literal["held", "failed", "out_of_stock"] = "held"
    message: str | None = None


class StructuredHandoverRequestSchema(BaseModel):
    session_id: str
    reason: str
    priority: Literal["low", "medium", "high", "urgent"] = "high"
    conversation_summary: str

    customer_name: str
    customer_phone: str
    customer_whatsapp: str | None = None
    customer_email: str | None = None
    preferred_contact_time: str = "evening"

    product_id: str | None = None
    size: str | None = None
    budget: float | None = None
    event_date: str | None = None
    event_type: str | None = None


class StructuredHandoverResponseSchema(BaseModel):
    ticket_id: str
    status: str = "created"
    assigned_to: str = "Style Consultant"
    expected_response_time: str = "within 2 hours"
