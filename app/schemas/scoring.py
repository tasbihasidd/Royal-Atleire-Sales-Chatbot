from __future__ import annotations
from pydantic import BaseModel, Field


class ProductScoreResult(BaseModel):
    product_id: str
    product_name: str
    total_score: float = Field(ge=0.0, le=1.0)
    
    # Weighted Score Breakdown
    occasion_score: float = Field(default=0.0, ge=0.0, le=0.25)
    season_score: float = Field(default=0.0, ge=0.0, le=0.20)
    budget_score: float = Field(default=0.0, ge=0.0, le=0.20)
    colour_score: float = Field(default=0.0, ge=0.0, le=0.15)
    stock_score: float = Field(default=0.0, ge=0.0, le=0.20)

    # Reasoning Vectors for LLM Prompt Injection
    reason_tags: list[str] = Field(default_factory=list)  # e.g. ["+NIKKAH", "+WinterWeight", "+Under150k", "+Size40Stock"]
    explanation_summary: str = ""
    caveats: list[str] = Field(default_factory=list)


class RecommendationCandidatePayload(BaseModel):
    top_candidates: list[ProductScoreResult] = Field(default_factory=list)
    raw_candidate_count: int = 0
