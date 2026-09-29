from __future__ import annotations

from pydantic import BaseModel, Field


class ProductSchema(BaseModel):
    """Locked product response contract — see plans/SCHEMAS.md section 1."""

    product_id: str
    name: str
    description: str | None = None
    price: float
    floor_price: float | None = None
    image_url: str | None = None

    fabric_id: str | None = None
    fabric: str | None = None
    pattern: str | None = None
    category: str | None = None
    season: str | None = None
    available_sizes: list[str] = Field(default_factory=list)
    available_colors: list[str] = Field(default_factory=list)
    occasion: str | None = None


class ProductSearchRequestSchema(BaseModel):
    """Locked product search request — see plans/SCHEMAS.md section 2."""

    product_type: str | None = None
    color: str | None = None
    occasion: str | None = None
    budget_max: float | None = None
    size: str | None = None
    fabric: str | None = None
    season: str | None = None
    in_stock: bool | None = None
    limit: int = 10
    offset: int = 0


class ProductSearchResponseSchema(BaseModel):
    products: list[ProductSchema] = Field(default_factory=list)
    total_count: int = 0
