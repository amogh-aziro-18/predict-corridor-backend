"""Operational recommendation API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RecommendationOut(BaseModel):
    id: str
    priority: str = Field(description="critical | high | medium | low")
    severity: str = Field(description="critical | high | medium | low")
    title: str
    description: str
    affected_asset: str
    affected_zone: str
    recommendation: str
    estimated_business_impact: str
    confidence: float = Field(ge=0.0, le=1.0)
    generated_at: datetime
    scenario_key: str | None = None
    rule_code: str | None = None
    source: str | None = Field(
        default=None,
        description=(
            "Origin family: telemetry | scenario | maintenance | weather | environment | "
            "resilience | cross_utility"
        ),
    )
    # Optional structured context (e.g. Phase 16.4 cross-utility impact for WO snapshot).
    context: dict[str, Any] | None = None
