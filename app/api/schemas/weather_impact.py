"""
Weather Impact Assessment API contract (Phase 8C).

Defines typed shapes for deterministic impact signals derived from
normalized weather observations via WeatherImpactService.

Signals MUST be based on measured weather values and explicit thresholds.
Do not use this contract for LLM-invented risk or fabricated probabilities.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.api.schemas.weather import WeatherLocationOut, WeatherProviderMetaOut


class WeatherImpactScope(StrEnum):
    """Geographic scope for which a signal is evaluated."""

    CORRIDOR = "corridor"
    ZONE = "zone"
    ASSET = "asset"


class WeatherImpactObservationKind(StrEnum):
    """Whether the underlying measurement is current or forecast."""

    CURRENT = "current"
    FORECAST = "forecast"


class WeatherImpactSignalType(StrEnum):
    """Categories of measurable weather conditions for impact rules."""

    PRECIPITATION = "precipitation"
    TEMPERATURE = "temperature"
    RELATIVE_HUMIDITY = "relative_humidity"
    WIND_SPEED = "wind_speed"
    WIND_GUST = "wind_gust"
    PRESSURE = "pressure"
    WEATHER_CODE = "weather_code"


class StaticAssetRiskMetadataOut(BaseModel):
    """
    Static seed/catalog asset risk labels for planning context.

    ``weather_exposure`` and ``flood_risk`` counts are NOT live weather-derived
    risk and NOT flood probability. ``utility_kind`` is typically ``all_enabled``
    when aggregating Water/Power/Gas/Communications registry labels in scope.
    """

    note: str
    utility_kind: str = Field(
        description=(
            "Scope of the static counts: historically a single utility; "
            "now 'all_enabled' for multi-utility registry aggregation."
        ),
    )
    zone_id: str | None = None
    asset_count: int
    weather_exposure: dict[str, int] = Field(
        default_factory=dict,
        description="Count of assets per static weather_exposure label.",
    )
    flood_risk: dict[str, int] = Field(
        default_factory=dict,
        description="Count of assets per static flood_risk label.",
    )


class WeatherImpactSignalOut(BaseModel):
    """
    A single deterministic weather impact signal.

    ``affected_asset_ids`` MUST be empty unless asset-level attribution is
    legitimately supported by the assessment logic (not inferred from corridor
    weather alone).
    """

    signal_type: WeatherImpactSignalType
    severity: str = Field(
        description="Operational severity: critical | high | medium | low | info"
    )
    scope: WeatherImpactScope
    zone_id: str | None = Field(
        default=None,
        description="Set when scope is zone (or zone context applies).",
    )
    affected_asset_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Asset IDs only when legitimately determinable. "
            "Empty for corridor/zone aggregate signals."
        ),
    )
    observation_kind: WeatherImpactObservationKind
    measured_value: float = Field(description="Observed or forecast numeric value.")
    unit: str = Field(description="Unit for measured_value (e.g. mm, °C, km/h).")
    threshold_rule: str = Field(
        description="Explicit rule that fired (e.g. precipitation_mm >= 10.0)."
    )
    explanation: str = Field(
        description="Human-readable explanation tied to the measurement and rule."
    )
    observed_at: datetime = Field(
        description="Timestamp of the underlying current observation or forecast point."
    )
    source: str = Field(
        description="Weather data provider identifier (e.g. open_meteo)."
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Optional confidence when justified by assessment methodology. "
            "Omit for pure threshold breaches on live measurements."
        ),
    )


class WeatherImpactAssessmentOut(BaseModel):
    """
    Aggregate weather impact assessment for a scope.

    Produced by WeatherImpactService from live weather API data and explicit
    rules. Static asset metadata is provided separately in
    ``static_metadata_context`` and is NOT mixed into signals as live risk.
    """

    location: WeatherLocationOut
    provider_meta: WeatherProviderMetaOut = Field(
        description="Provider/cache metadata for current weather input.",
    )
    forecast_provider_meta: WeatherProviderMetaOut = Field(
        description="Provider/cache metadata for forecast weather input.",
    )
    assessed_at: datetime
    scope: WeatherImpactScope
    zone_id: str | None = None
    forecast_hours: int = Field(description="Forecast horizon used for assessment.")
    overall_severity: str = Field(
        description=(
            "Highest severity across signals: none | info | low | medium | high | critical. "
            "'none' means no threshold breaches."
        ),
    )
    signals: list[WeatherImpactSignalOut] = Field(default_factory=list)
    static_metadata_context: StaticAssetRiskMetadataOut | None = None
