"""Weather provider abstraction — utility-agnostic external weather access."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class WeatherLocation:
    """Resolved lookup point for external weather providers."""

    latitude: float
    longitude: float
    scope: str  # corridor | zone
    zone_id: str | None
    asset_count: int
    utility_kind: str
    method: str
    city: str | None = None
    country: str | None = None
    display_name: str | None = None
    location_note: str | None = None


@dataclass(frozen=True)
class NormalizedCurrentWeather:
    observed_at: datetime
    timezone: str
    latitude: float
    longitude: float
    temperature_c: float
    relative_humidity_pct: float
    precipitation_mm: float
    wind_speed_kmh: float
    wind_direction_deg: float
    pressure_hpa: float
    weather_code: int
    provider: str
    provider_fetched_at: datetime


@dataclass(frozen=True)
class NormalizedForecastPoint:
    timestamp: datetime
    temperature_c: float
    relative_humidity_pct: float
    precipitation_mm: float
    precipitation_probability_pct: float | None
    wind_speed_kmh: float
    wind_gusts_kmh: float | None
    pressure_hpa: float
    weather_code: int


@dataclass(frozen=True)
class NormalizedWeatherForecast:
    timezone: str
    latitude: float
    longitude: float
    hourly: list[NormalizedForecastPoint]
    provider: str
    provider_fetched_at: datetime


class WeatherProvider(ABC):
    """Pluggable external weather source."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable provider identifier (e.g. open_meteo)."""

    @abstractmethod
    def fetch_current(self, location: WeatherLocation) -> NormalizedCurrentWeather:
        """Fetch current conditions for a resolved location."""

    @abstractmethod
    def fetch_forecast(
        self,
        location: WeatherLocation,
        *,
        hours: int = 48,
    ) -> NormalizedWeatherForecast:
        """Fetch hourly forecast points for a resolved location."""


class WeatherProviderError(Exception):
    """Raised when an external weather provider call fails."""

    def __init__(self, message: str, *, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.cause = cause


def provider_payload_debug(payload: dict[str, Any]) -> dict[str, Any]:
    """Trim provider payload for logs/tests — not exposed via API."""
    return {
        "latitude": payload.get("latitude"),
        "longitude": payload.get("longitude"),
        "timezone": payload.get("timezone"),
    }
