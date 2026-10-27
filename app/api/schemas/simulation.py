"""Operational what-if simulation API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


class WhatIfSimulationIn(BaseModel):
    asset_id: str = Field(min_length=1)
    scenario_key: str = Field(min_length=1)
    horizon_hours: int = Field(default=24, description="Supported: 6, 12, 24, 48")


class ParameterWhatIfIn(BaseModel):
    asset_id: str = Field(min_length=1)
    parameter: str = Field(min_length=1)
    delta_percent: float | None = Field(
        default=None,
        description="Relative change applied to live baseline (e.g. 10 = +10%).",
    )
    absolute_value: float | None = Field(
        default=None,
        description="Absolute projected value (mutually exclusive with delta_percent).",
    )

    @model_validator(mode="after")
    def _one_change(self) -> ParameterWhatIfIn:
        if self.delta_percent is None and self.absolute_value is None:
            raise ValueError("Provide delta_percent or absolute_value")
        if self.delta_percent is not None and self.absolute_value is not None:
            raise ValueError("Provide only one of delta_percent or absolute_value")
        return self


class MetricValueOut(BaseModel):
    metric_key: str
    value: float | None = None
    unit: str = ""
    recorded_at: datetime | None = None


class MetricDeltaOut(BaseModel):
    metric_key: str
    from_value: float | None = None
    to_value: float | None = None
    direction: str
    absolute_change: float | None = None
    percent_change: float | None = None
    note: str | None = None


class SimulationAssetRefOut(BaseModel):
    asset_id: str
    asset_name: str
    utility: str | None = None
    asset_type: str
    zone: str
    status: str | None = None
    health: float | None = None
    criticality: str | None = None
    risk: str | None = None
    predicted_failure_days: float | None = None


class AffectedAssetOut(BaseModel):
    asset_id: str
    asset_name: str
    asset_type: str
    zone: str
    is_primary: bool = False
    impacted_metrics: list[dict[str, Any]] = Field(default_factory=list)


class ScenarioRefOut(BaseModel):
    key: str
    name: str
    description: str
    category: str
    default_severity: str


class DependencyEndpointRefOut(BaseModel):
    """Asset endpoint on an explicit dependency edge (no impact calculation)."""

    dependency_id: str
    asset_id: str
    asset_name: str
    utility: str
    zone: str
    dependency_type: str


class DependenciesOut(BaseModel):
    """
    Explicit dependency edges for a simulation target asset (Phase 16.0).

    - depends_on: assets this target depends on (incoming supplies edges)
    - depended_by: assets that depend on this target (outgoing supplies edges)
    """

    available: bool = False
    message: str = "Dependency data not available"
    depends_on: list[DependencyEndpointRefOut] = Field(default_factory=list)
    depended_by: list[DependencyEndpointRefOut] = Field(default_factory=list)


class DependencyImpactAssetRefOut(BaseModel):
    """Potentially affected dependent asset (hypothetical — not declared failed)."""

    asset_id: str
    asset_name: str
    utility: str
    zone: str
    impact_kind: str  # direct | propagated
    depth: int = 0
    posture: str = "potentially_affected"


class DependencyPathSummaryOut(BaseModel):
    asset_id: str
    depth: int
    impact_kind: str
    path_asset_ids: list[str] = Field(default_factory=list)
    path_dependency_ids: list[str] = Field(default_factory=list)


class DependencyImpactSummaryOut(BaseModel):
    """
    Phase 16.3 structured dependency impact for Digital Twin parameter what-if.

    Attached when projected_condition is CRITICAL. Payload is produced from
    CrossUtilityImpactService only (no second traversal). Hypothetical — does not
    mutate operational state or generate recommendations.
    """

    assessed: bool = False
    hypothetical: bool = True
    mutates_operational_state: bool = False
    trigger_condition: str | None = None
    projected_condition: str | None = None
    source_asset_id: str | None = None
    source_asset_name: str | None = None
    source_utility: str | None = None
    source_zone: str | None = None
    direct_impacts: list[DependencyImpactAssetRefOut] = Field(default_factory=list)
    propagated_impacts: list[DependencyImpactAssetRefOut] = Field(default_factory=list)
    affected_assets: list[DependencyImpactAssetRefOut] = Field(default_factory=list)
    affected_asset_count: int = 0
    affected_utilities: list[str] = Field(default_factory=list)
    affected_zones: list[str] = Field(default_factory=list)
    # Backward-compatible flat id list (Phase 16.1 consumers).
    direct_impact_asset_ids: list[str] = Field(default_factory=list)
    dependency_paths: list[DependencyPathSummaryOut] = Field(default_factory=list)
    explanation: str = ""
    note: str = (
        "Hypothetical potential dependency impact only — not a declared multi-utility outage. "
        "No telemetry, alerts, work orders, or recommendations were modified."
    )


class WhatIfSimulationOut(BaseModel):
    simulation_id: str
    mode: str
    physics_based: bool
    disclaimer: str
    generated_at: datetime
    horizon_hours: int
    intensity: float
    status: str
    target_asset: SimulationAssetRefOut
    scenario: ScenarioRefOut
    baseline: dict[str, Any]
    predicted: dict[str, Any]
    affected_assets: list[AffectedAssetOut] = Field(default_factory=list)
    affected_zones: list[str] = Field(default_factory=list)
    operational_risk: str
    service_impact: str
    mitigations: list[str] = Field(default_factory=list)
    dependencies: DependenciesOut


class SimulationBaselineOut(BaseModel):
    asset_id: str
    asset_name: str
    utility: str
    asset_type: str
    zone: str
    status: str
    health: float
    criticality: str
    risk: str | None = None
    predicted_failure_days: float | None = None
    metrics: list[MetricValueOut] = Field(default_factory=list)
    captured_at: datetime


class ParameterOptionOut(BaseModel):
    parameter: str
    available: bool
    unit: str = ""
    note: str = ""


class SimulationParametersOut(BaseModel):
    asset_id: str
    utility: str
    asset_type: str
    parameters: list[ParameterOptionOut] = Field(default_factory=list)
    disclaimer: str


class ParameterWhatIfOut(BaseModel):
    simulation_id: str
    mode: str
    physics_based: bool
    disclaimer: str
    generated_at: datetime
    status: str
    target_asset: SimulationAssetRefOut
    parameter: str
    available: bool
    baseline_value: float | None = None
    projected_value: float | None = None
    unit: str = ""
    delta_percent: float | None = None
    absolute_value: float | None = None
    projected_condition: str
    potential_alert: bool
    potential_risk: str
    explanation: str
    recommended_action: str
    rule_refs: list[str] = Field(default_factory=list)
    dependencies: DependenciesOut
    dependency_impact: DependencyImpactSummaryOut | None = None
