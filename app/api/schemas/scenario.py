"""Scenario API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ScenarioOut(BaseModel):
    key: str
    name: str
    description: str
    category: str
    default_severity: str


class ZoneScenarioStateOut(BaseModel):
    zone_id: str
    scenario_key: str
    intensity: float = Field(ge=0.0, le=1.0)
    name: str | None = None
    default_severity: str | None = None


class CurrentScenarioOut(BaseModel):
    key: str
    name: str
    description: str
    category: str
    default_severity: str
    intensity: float = Field(ge=0.0, le=1.0)
    previous_key: str | None = None
    selected_at: datetime
    updated_at: datetime
    # Empty list when only corridor-wide scenario is active (backward compatible).
    zone_scenarios: list[ZoneScenarioStateOut] = Field(default_factory=list)


class ScenarioSelectOut(BaseModel):
    key: str
    name: str
    message: str
    intensity: float


class ZoneScenarioConfigureIn(BaseModel):
    """Map of zone_id → ScenarioKey value (e.g. zone-c → pump_failure)."""

    zones: dict[str, str] = Field(
        default_factory=dict,
        description="zone_id → scenario_key; empty clears per-zone overrides",
    )


class ZoneScenarioConfigureOut(BaseModel):
    message: str
    zone_scenarios: list[ZoneScenarioStateOut]
    raw: dict[str, Any] = Field(default_factory=dict)
