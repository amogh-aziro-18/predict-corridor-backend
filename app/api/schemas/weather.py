"""Normalized weather API schemas (Phase 8A — external provider data only)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class WeatherLocationOut(BaseModel):
    latitude: float
    longitude: float
    timezone: str
    scope: str = Field(description="corridor | zone")
    zone_id: str | None = None
    asset_count: int
    utility_kind: str = Field(
        description="Utility context for registry awareness (not a coordinate invent)."
    )
    method: str = Field(
        description=(
            "How coordinates were resolved "
            "(e.g. configured_operational_location, centroid_of_assets)."
        )
    )
    city: str | None = Field(
        default=None,
        description="Operational city label (e.g. Delhi).",
    )
    country: str | None = Field(
        default=None,
        description="Operational country label (e.g. India).",
    )
    display_name: str | None = Field(
        default=None,
        description="Human-readable operational location (e.g. Delhi, India).",
    )
    location_note: str | None = Field(
        default=None,
        description="Honesty note about how/where weather-environment is sourced.",
    )


class WeatherProviderMetaOut(BaseModel):
    provider: str
    fetched_at: datetime
    cache_status: str = Field(description="fresh | cached | stale")
    cache_expires_at: datetime | None = None
    stale: bool = False


class CurrentWeatherOut(BaseModel):
    location: WeatherLocationOut
    provider_meta: WeatherProviderMetaOut
    observed_at: datetime
    temperature_c: float
    relative_humidity_pct: float
    precipitation_mm: float
    wind_speed_kmh: float
    wind_direction_deg: float
    pressure_hpa: float
    weather_code: int


class ForecastPointOut(BaseModel):
    timestamp: datetime
    temperature_c: float
    relative_humidity_pct: float
    precipitation_mm: float
    precipitation_probability_pct: float | None = None
    wind_speed_kmh: float
    wind_gusts_kmh: float | None = None
    pressure_hpa: float
    weather_code: int


class WeatherForecastOut(BaseModel):
    location: WeatherLocationOut
    provider_meta: WeatherProviderMetaOut
    hourly: list[ForecastPointOut] = Field(default_factory=list)
