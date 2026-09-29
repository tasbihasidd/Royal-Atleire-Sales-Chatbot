from __future__ import annotations

from pydantic import BaseModel, Field


class FabricSchema(BaseModel):
    """
    Locked fabric response for custom / bespoke — see plans/SCHEMAS.md section 3.

    Confirmed exclusions: no price, no meters, no weight.
    product.fabric_id must equal fabric.catalog_code.
    """

    catalog_code: str
    name: str
    available_colors: list[str] = Field(default_factory=list)
    description: str | None = None
    image_url: str | None = None
    fabric_type: str | None = None
    pattern: str | None = None
    category: str | None = None
    season: str | None = None
    embroidery: str | None = None


class FabricSearchRequestSchema(BaseModel):
    """Fabric search filters aligned to locked fabric fields."""

    fabric_type: str | None = None
    color: str | None = None
    category: str | None = None
    season: str | None = None
    pattern: str | None = None
    embroidery: str | None = None
    limit: int = 10
    offset: int = 0


class FabricSearchResponseSchema(BaseModel):
    fabrics: list[FabricSchema] = Field(default_factory=list)
    total_count: int = 0
