"""
Environment Impact Assessment API contract (Phase 9C).

Deterministic impact signals derived from EnvironmentSignal observations.
Not an observation layer — do not confuse with EnvironmentSignal.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.api.schemas.environment_signal import EnvironmentObservationKind, EnvironmentSignalScope
from app.api.schemas.weather import WeatherLocationOut, WeatherProviderMetaOut


class EnvironmentImpactSignalType(StrEnum):
    """Air-quality signal types assessed for operational impact."""

    AQI = "aqi"
    PM25 = "pm25"
    PM10 = "pm10"
    NO2 = "no2"
    SO2 = "so2"
    CO = "co"
    O3 = "o3"


class EnvironmentImpactSignalOut(BaseModel):
    """
    One deterministic environmental impact signal (threshold breach).

    No affected_asset_ids — corridor/zone centroid assessment only.
    """

    signal_type: EnvironmentImpactSignalType | str
    severity: str = Field(
        description="Operational severity: critical | high | medium | low | info",
    )
    scope: EnvironmentSignalScope
    zone_id: str | None = None
    observation_kind: EnvironmentObservationKind
    measured_value: float
    unit: str
    threshold_rule: str
    explanation: str
    observed_at: datetime
    source: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Evidence extras (e.g. aqi_standard=us_aqi). Never CPCB claims.",
    )

    @model_validator(mode="after")
    def _validate_scope_zone(self) -> EnvironmentImpactSignalOut:
        if self.scope == EnvironmentSignalScope.ZONE and not self.zone_id:
            raise ValueError("zone_id is required when scope is 'zone'")
        return self


class EnvironmentImpactEvidenceOut(BaseModel):
    """Assessment methodology / evidence context (not live regulatory certification)."""

    note: str
    threshold_basis: str
    aqi_policy: str = Field(
        description="How AQI standards are applied (US AQI only when explicitly identified).",
    )


class EnvironmentImpactAssessmentOut(BaseModel):
    """
    Aggregate environmental impact assessment for a corridor or zone scope.

    Consumes EnvironmentService observations only — never calls the AQI provider
    directly and never fabricates measurements.
    """

    location: WeatherLocationOut | None = None
    provider_meta: WeatherProviderMetaOut | None = None
    assessed_at: datetime
    scope: EnvironmentSignalScope
    zone_id: str | None = None
    utility_kind: str | None = None
    forecast_hours: int = Field(description="Forecast horizon requested from EnvironmentService.")
    observation_kinds: list[EnvironmentObservationKind] = Field(
        default_factory=list,
        description="Observation kinds present in the assessed EnvironmentSignal set.",
    )
    overall_severity: str = Field(
        description=(
            "Highest severity across impact signals: none | info | low | medium | high | critical. "
            "'none' means no threshold breaches."
        ),
    )
    signals: list[EnvironmentImpactSignalOut] = Field(default_factory=list)
    evidence: EnvironmentImpactEvidenceOut
    note: str | None = Field(
        default=None,
        description="Optional status (e.g. stale observation input).",
    )

    @model_validator(mode="after")
    def _validate_scope_zone(self) -> EnvironmentImpactAssessmentOut:
        if self.scope == EnvironmentSignalScope.ZONE and not self.zone_id:
            raise ValueError("zone_id is required when scope is 'zone'")
        return self
