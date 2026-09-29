from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CustomerProfileSchema(BaseModel):
    session_id: str
    customer_name: str | None = None
    customer_phone: str | None = None
    customer_email: str | None = None

    event_type: Literal["NIKKAH", "BARAT", "WALIMA", "MEHNDI", "ENGAGEMENT", "RECEPTION"] | str | None = None
    product_type: str | None = None
    selected_variation_id: str | None = None
    selected_variation_name: str | None = None
    selected_product_variation_id: str | None = None
    selected_product_variation_name: str | None = None
    wedding_date: str | None = None
    derived_season: Literal["winter", "summer", "autumn", "spring"] | str | None = None

    budget: float | None = None
    jacket_size: str | None = None
    size: str | None = None
    height: str | None = None
    chest: str | None = None
    waist: str | None = None
    skin_tone: Literal["fair", "wheatish", "deep", "tan"] | str | None = None

    color: str | None = None
    preferred_colors: list[str] = Field(default_factory=list)
    preferred_fabrics: list[str] = Field(default_factory=list)

    sales_stage: Literal[
        "discovery",
        "recommendation",
        "detail",
        "availability",
        "styling",
        "objection",
        "negotiation",
        "closing",
        "handover",
        "customization",
    ] = "discovery"
    buying_intent: Literal["browsing", "comparing", "considering", "ready_to_buy"] = "browsing"

    products_shown_ids: list[str] = Field(default_factory=list)
    selected_product_id: str | None = None
    rejected_product_ids: list[str] = Field(default_factory=list)
    selected_fabric_catalog_code: str | None = None
    declined_slots: list[str] = Field(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        if self.color and not self.preferred_colors:
            self.preferred_colors = [self.color]
        elif self.preferred_colors and not self.color:
            self.color = self.preferred_colors[0]
        if self.size and not self.jacket_size:
            self.jacket_size = self.size
        elif self.jacket_size and not self.size:
            self.size = self.jacket_size

    @property
    def missing_entities(self) -> list[str]:
        missing = []
        if not self.event_type:
            missing.append("event_type")
        if not self.wedding_date:
            missing.append("wedding_date")
        if not self.budget:
            missing.append("budget")
        if not self.jacket_size and not (self.height and self.chest):
            missing.append("size")
        return missing

    @property
    def missing_entities_count(self) -> int:
        return len(self.missing_entities)
