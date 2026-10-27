"""Water / Power / Gas / corridor dashboard response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CorridorUtilityPostureOut(BaseModel):
    """Per-utility snapshot used to compose corridor operational posture."""

    utility: str
    available: bool
    posture: str
    overall_health: float | None = None
    overall_condition: str | None = None
    asset_count: int = 0
    critical_assets: int = 0
    active_alerts: int = 0
    api_status: str = "unknown"


class CorridorDependencyAffectedAssetOut(BaseModel):
    """Downstream asset potentially exposed via explicit supplies dependency."""

    asset_id: str
    asset_name: str = ""
    utility: str = ""
    zone: str = ""
    impact_kind: str = ""
    depth: int = 0
    posture: str = "potentially_affected"


class CorridorDependencyPathOut(BaseModel):
    """Path from CrossUtilityImpactService (no second traversal)."""

    asset_id: str
    depth: int = 0
    impact_kind: str = ""
    path_asset_ids: list[str] = Field(default_factory=list)
    path_dependency_ids: list[str] = Field(default_factory=list)


class CorridorDependencySourceOut(BaseModel):
    """One live critical/unavailable source with modeled downstream exposure."""

    source_asset_id: str
    source_utility: str = ""
    source_zone: str = ""
    condition: str = ""
    affected_assets: list[CorridorDependencyAffectedAssetOut] = Field(default_factory=list)
    affected_utilities: list[str] = Field(default_factory=list)
    affected_zones: list[str] = Field(default_factory=list)
    dependency_paths: list[CorridorDependencyPathOut] = Field(default_factory=list)
    hypothetical: bool = True
    assessment_kind: str = "explicit_dependency_impact"


class CorridorDependencyExposureOut(BaseModel):
    """
    Aggregated cross-utility dependency exposure for the corridor.

    Evidence only — does not elevate overall corridor posture.
    """

    available: bool = False
    hypothetical: bool = True
    contributes_to_overall_posture: bool = False
    source_asset_ids: list[str] = Field(default_factory=list)
    affected_asset_ids: list[str] = Field(default_factory=list)
    affected_utilities: list[str] = Field(default_factory=list)
    affected_zones: list[str] = Field(default_factory=list)
    sources: list[CorridorDependencySourceOut] = Field(default_factory=list)
    dependency_paths: list[CorridorDependencyPathOut] = Field(default_factory=list)
    explanation: str = ""
    note: str = (
        "Modeled dependency exposure from explicit supplies edges via "
        "CrossUtilityImpactService. Does not elevate corridor operational posture."
    )


class CorridorDashboardOut(BaseModel):
    """
    Corridor-level operational posture composed from live Water + Power + Gas dashboards.

    Categorical posture only — not a fabricated scientific 0–100 corridor health index.
    Phase 16.6 adds optional dependency_exposure + situation_summary (evidence only).
    """

    overall_posture: str
    assessment_kind: str = "composed_utility_operational_posture"
    explanation: str
    drivers: list[str] = Field(default_factory=list)
    utilities: list[CorridorUtilityPostureOut] = Field(default_factory=list)
    total_assets: int = 0
    total_critical_assets: int = 0
    total_active_alerts: int = 0
    evaluated_at: datetime | None = None
    # Phase 16.6 — additive cross-utility situation (does not change posture math).
    dependency_exposure: CorridorDependencyExposureOut | None = None
    situation_summary: str = ""


class ZoneWaterSummaryOut(BaseModel):
    zone_id: str
    zone_name: str
    asset_count: int
    average_health: float
    average_pressure: float | None = None
    average_flow: float | None = None
    degraded_assets: int = 0
    # Mean of tank_level / reservoir_level (or profile current_level) for the zone.
    # Null when the zone has no level-capable telemetry.
    average_level: float | None = None


class WaterDashboardOut(BaseModel):
    overall_health: float
    average_pressure: float | None = None
    average_flow: float | None = None
    critical_assets: int
    active_alerts: int
    zone_summary: list[ZoneWaterSummaryOut] = Field(default_factory=list)
    asset_count: int = 0
    utility: str = "water"
    # Status-strip fields (reuse existing frontend StatusCards)
    last_telemetry_at: datetime | None = None
    live_telemetry_points: int = 0
    data_freshness_seconds: float | None = None
    simulator_status: str = "unknown"
    backend_status: str = "online"
    api_status: str = "healthy"


class ZonePowerSummaryOut(BaseModel):
    """Zone roll-up from Power rule-based condition assessment (not ML)."""

    zone_id: str
    zone_name: str
    asset_count: int
    overall_condition: str
    average_health: float
    affected_assets: list[str] = Field(default_factory=list)
    major_drivers: list[str] = Field(default_factory=list)
    active_alert_count: int = 0
    evaluated_at: datetime | None = None
    assessment_kind: str = "rule_based_condition_assessment"


class PowerDashboardOut(BaseModel):
    """
    Power operations dashboard — rule-based condition assessment from live telemetry.

    Not ML predictions. overall_health is the documented weighted operational score.
    """

    overall_health: float
    overall_condition: str
    critical_assets: int
    active_alerts: int
    zone_summary: list[ZonePowerSummaryOut] = Field(default_factory=list)
    asset_count: int = 0
    utility: str = "power"
    assessment_kind: str = "rule_based_condition_assessment"
    last_telemetry_at: datetime | None = None
    live_telemetry_points: int = 0
    data_freshness_seconds: float | None = None
    simulator_status: str = "unknown"
    backend_status: str = "online"
    api_status: str = "healthy"


class ZoneGasSummaryOut(BaseModel):
    """Zone roll-up from Gas rule-based condition assessment (not ML)."""

    zone_id: str
    zone_name: str
    asset_count: int
    overall_condition: str
    average_health: float
    affected_assets: list[str] = Field(default_factory=list)
    major_drivers: list[str] = Field(default_factory=list)
    active_alert_count: int = 0
    average_pressure_bar: float | None = None
    average_flow_nm3_h: float | None = None
    evaluated_at: datetime | None = None
    assessment_kind: str = "rule_based_condition_assessment"


class GasDashboardOut(BaseModel):
    """
    Gas operations dashboard — rule-based condition from live Gas telemetry.

    Not ML predictions. overall_health is the Gas rule-based operational score.
    """

    overall_health: float
    overall_condition: str
    critical_assets: int
    active_alerts: int
    zone_summary: list[ZoneGasSummaryOut] = Field(default_factory=list)
    asset_count: int = 0
    utility: str = "gas"
    assessment_kind: str = "rule_based_condition_assessment"
    last_telemetry_at: datetime | None = None
    live_telemetry_points: int = 0
    data_freshness_seconds: float | None = None
    simulator_status: str = "unknown"
    backend_status: str = "online"
    api_status: str = "healthy"


class ZoneCommunicationsSummaryOut(BaseModel):
    """Zone roll-up from Communications rule-based condition assessment (not ML)."""

    zone_id: str
    zone_name: str
    asset_count: int
    overall_condition: str
    average_health: float
    affected_assets: list[str] = Field(default_factory=list)
    major_drivers: list[str] = Field(default_factory=list)
    active_alert_count: int = 0
    evaluated_at: datetime | None = None
    assessment_kind: str = "rule_based_condition_assessment"


class CommunicationsDashboardOut(BaseModel):
    """
    Communications operations dashboard — rule-based condition from live telemetry.

    Not ML predictions. overall_health is the Communications operational score.
    """

    overall_health: float
    overall_condition: str
    critical_assets: int
    active_alerts: int
    zone_summary: list[ZoneCommunicationsSummaryOut] = Field(default_factory=list)
    asset_count: int = 0
    utility: str = "communications"
    assessment_kind: str = "rule_based_condition_assessment"
    last_telemetry_at: datetime | None = None
    live_telemetry_points: int = 0
    data_freshness_seconds: float | None = None
    simulator_status: str = "unknown"
    backend_status: str = "online"
    api_status: str = "healthy"
