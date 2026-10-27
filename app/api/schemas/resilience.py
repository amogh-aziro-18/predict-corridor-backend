"""
Resilience Assessment API contract (Phase 10A).

Posture-first composition of weather impact, environment impact, scenario
state, and static asset posture. Not a 0–100 score and not a threshold engine.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class ResilienceScope(StrEnum):
    CORRIDOR = "corridor"
    ZONE = "zone"


class ResilienceDriverSource(StrEnum):
    WEATHER = "weather"
    ENVIRONMENT = "environment"
    SCENARIO = "scenario"
    ASSET_POSTURE = "asset_posture"
    # Phase 16.5 — explanatory only; does not elevate overall posture.
    DEPENDENCY_EXPOSURE = "dependency_exposure"


class ResilienceDriverOut(BaseModel):
    """One synthesized driver (not one row per underlying weather/env signal)."""

    source: ResilienceDriverSource | str
    severity: str = Field(
        description="none | low | medium | high | critical (info mapped to low where needed)",
    )
    explanation: str
    evidence: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured evidence for this driver (counts, signal summaries, keys).",
    )


class ResilienceInputsAvailabilityOut(BaseModel):
    weather_available: bool = False
    environment_available: bool = False
    scenario_available: bool = False
    assets_available: bool = False
    # Phase 16.5 — True when dependency exposure was evaluated (may still be empty).
    dependencies_available: bool = False


class ResilienceAssessmentOut(BaseModel):
    """
    Corridor or zone resilience posture assessment.

    POC operational posture model — not a scientifically validated index.
    """

    scope: ResilienceScope
    zone_id: str | None = None
    utility_kind: str
    overall_posture: str = Field(
        description="Ordered posture: none | low | medium | high | critical",
    )
    overall_severity: str = Field(
        description="Alias of overall_posture for platform severity vocabulary consistency.",
    )
    drivers: list[ResilienceDriverOut] = Field(default_factory=list)
    evidence: list[str] = Field(
        default_factory=list,
        description="Human-readable evidence bullets (no fabricated measurements).",
    )
    assessed_at: datetime
    forecast_hours: int = Field(
        description="Horizon requested from weather/environment impact services.",
    )
    inputs_available: ResilienceInputsAvailabilityOut
    inputs_stale: bool = False
    note: str | None = Field(
        default=None,
        description="Honest status (POC model note, insufficient inputs, stale inputs).",
    )

    @model_validator(mode="after")
    def _validate_scope_zone(self) -> ResilienceAssessmentOut:
        if self.scope == ResilienceScope.ZONE and not self.zone_id:
            raise ValueError("zone_id is required when scope is 'zone'")
        if self.overall_severity != self.overall_posture:
            # Keep fields aligned when callers set only one.
            object.__setattr__(self, "overall_severity", self.overall_posture)
        return self


class ResilienceContextOut(BaseModel):
    """Corridor + per-zone resilience assessments for GET /api/resilience."""

    utility_kind: str
    assessed_at: datetime
    forecast_hours: int
    corridor: ResilienceAssessmentOut
    zones: list[ResilienceAssessmentOut] = Field(default_factory=list)
    note: str = Field(
        description=(
            "POC operational posture composed from weather/environment impact, "
            "scenario state, and static asset posture. Not a 0–100 resilience index."
        ),
    )
