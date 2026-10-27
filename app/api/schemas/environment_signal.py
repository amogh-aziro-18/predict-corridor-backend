"""
EnvironmentSignal observation contract (Phase 9A).

Represents ONE normalized environmental observation — not an impact assessment,
recommendation, alert, or risk verdict.

Do not confuse with WeatherImpactSignalOut (impact layer).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.api.schemas.weather import WeatherLocationOut, WeatherProviderMetaOut


class EnvironmentSignalCategory(StrEnum):
    """High-level environmental domain family."""

    WEATHER = "weather"
    AIR_QUALITY = "air_quality"


class EnvironmentObservationKind(StrEnum):
    """Temporal character of the observation."""

    CURRENT = "current"
    FORECAST = "forecast"
    HISTORICAL = "historical"


class EnvironmentSignalScope(StrEnum):
    """
    Geographic scope for an observation.

    Asset scope is intentionally omitted in Phase 9A — centroid/corridor/zone only.
    """

    CORRIDOR = "corridor"
    ZONE = "zone"


class EnvironmentSignal(BaseModel):
    """
    One normalized environmental observation.

    ``utility_kind`` identifies which utility's geolocated assets informed the
    location context — it does not mean the measurement is utility-specific.
    """

    category: EnvironmentSignalCategory | str = Field(
        description='Domain family, e.g. "weather" | "air_quality".',
    )
    signal_type: str = Field(
        min_length=1,
        description=(
            "Observation key, e.g. temperature, humidity, precipitation, "
            "wind_speed, pressure, aqi, pm25, pm10, no2, so2, co, o3."
        ),
    )
    measured_value: float = Field(description="Numeric measured or forecast value.")
    unit: str = Field(min_length=1, description="Unit for measured_value.")
    observation_kind: EnvironmentObservationKind
    observed_at: datetime
    source: str = Field(
        min_length=1,
        description="Provider identifier (e.g. future air-quality provider name).",
    )
    scope: EnvironmentSignalScope
    zone_id: str | None = Field(
        default=None,
        description="Required when scope is zone; omit for corridor.",
    )
    utility_kind: str = Field(
        min_length=1,
        description=(
            "Utility context for location resolution (water, power, gas, ...). "
            "Not a hard-coded Water-only field."
        ),
    )
    location: WeatherLocationOut = Field(
        description="Resolved lookup location (reuse weather location conventions).",
    )
    provider_meta: WeatherProviderMetaOut | None = Field(
        default=None,
        description="Optional provider/cache metadata when available.",
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional confidence when the provider justifies it.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific extras (WMO code, AQI band, etc.).",
    )

    @model_validator(mode="after")
    def _validate_scope_zone(self) -> EnvironmentSignal:
        if self.scope == EnvironmentSignalScope.ZONE:
            if not self.zone_id:
                raise ValueError("zone_id is required when scope is 'zone'")
        return self


class EnvironmentObservationSet(BaseModel):
    """
    Bundle of EnvironmentSignal observations for a scope.

    Used for honest empty responses when no environment provider is configured.
    """

    scope: EnvironmentSignalScope
    zone_id: str | None = None
    utility_kind: str | None = Field(
        default=None,
        description="Utility context when known; omit when no location resolved.",
    )
    location: WeatherLocationOut | None = None
    provider_meta: WeatherProviderMetaOut | None = None
    observed_at: datetime | None = Field(
        default=None,
        description="Shared observation time when applicable; null if no provider data.",
    )
    generated_at: datetime = Field(
        description="When this observation set was assembled by the service.",
    )
    signals: list[EnvironmentSignal] = Field(default_factory=list)
    note: str | None = Field(
        default=None,
        description="Human-readable status (e.g. no provider configured).",
    )

    @model_validator(mode="after")
    def _validate_scope_zone(self) -> EnvironmentObservationSet:
        if self.scope == EnvironmentSignalScope.ZONE:
            if not self.zone_id:
                raise ValueError("zone_id is required when scope is 'zone'")
        return self
