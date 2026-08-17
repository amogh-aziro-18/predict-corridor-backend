"""Utility API schemas (aligned with frontend UtilitySummary)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SeriesPointOut(BaseModel):
    label: str
    value: float
    forecast: float | None = None


class UtilitySummaryOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    kind: str
    name: str
    zones: list[str] = Field(default_factory=list)
    operational_health: float = Field(serialization_alias="operationalHealth")
    forecast_health: float = Field(serialization_alias="forecastHealth")
    forecast_window: str = Field(serialization_alias="forecastWindow")
    weather_impact: str = Field(serialization_alias="weatherImpact")
    disaster_risk: str = Field(serialization_alias="disasterRisk")
    sla_compliance: float = Field(serialization_alias="slaCompliance")
    continuity: float
    recovery_priority: int = Field(serialization_alias="recoveryPriority")
    trend: str
    trend_delta: float = Field(serialization_alias="trendDelta")
    alerts: int
    assets: int
    telemetry_coverage: float = Field(serialization_alias="telemetryCoverage")
    data_freshness: str = Field(serialization_alias="dataFreshness")
    series: list[SeriesPointOut] = Field(default_factory=list)
