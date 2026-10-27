"""Open-Meteo forecast API adapter (keyless POC provider)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from app.integrations.weather.base import (
    NormalizedCurrentWeather,
    NormalizedForecastPoint,
    NormalizedWeatherForecast,
    WeatherLocation,
    WeatherProvider,
    WeatherProviderError,
)

CURRENT_VARIABLES = (
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "wind_speed_10m",
    "wind_direction_10m",
    "surface_pressure",
    "weather_code",
)

HOURLY_VARIABLES = (
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "precipitation_probability",
    "wind_speed_10m",
    "wind_gusts_10m",
    "surface_pressure",
    "weather_code",
)


def _parse_provider_time(value: str) -> datetime:
    """Parse Open-Meteo ISO timestamps into UTC-aware datetimes."""
    if value.endswith("Z"):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _require_number(payload: dict[str, Any], key: str) -> float:
    raw = payload.get(key)
    if raw is None:
        raise WeatherProviderError(f"Open-Meteo response missing required field: {key}")
    return float(raw)


def _optional_number(payload: dict[str, Any], key: str) -> float | None:
    raw = payload.get(key)
    if raw is None:
        return None
    return float(raw)


class OpenMeteoWeatherProvider(WeatherProvider):
    """
    https://open-meteo.com/en/docs — Forecast API.

    Uses a single forecast request for current + hourly series.
    """

    def __init__(
        self,
        *,
        base_url: str = "https://api.open-meteo.com/v1/forecast",
        timeout_seconds: float = 15.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._client = client

    @property
    def name(self) -> str:
        return "open_meteo"

    def fetch_current(self, location: WeatherLocation) -> NormalizedCurrentWeather:
        payload = self._request(
            location,
            include_current=True,
            include_hourly=False,
            forecast_days=1,
        )
        return self._normalize_current(payload)

    def fetch_forecast(
        self,
        location: WeatherLocation,
        *,
        hours: int = 48,
    ) -> NormalizedWeatherForecast:
        forecast_days = min(16, max(1, (hours + 23) // 24))
        payload = self._request(
            location,
            include_current=False,
            include_hourly=True,
            forecast_days=forecast_days,
        )
        return self._normalize_forecast(payload, hours=hours)

    def _request(
        self,
        location: WeatherLocation,
        *,
        include_current: bool,
        include_hourly: bool,
        forecast_days: int,
    ) -> dict[str, Any]:
        params: dict[str, str | int | float] = {
            "latitude": location.latitude,
            "longitude": location.longitude,
            "timezone": "auto",
            "wind_speed_unit": "kmh",
            "forecast_days": forecast_days,
        }
        if include_current:
            params["current"] = ",".join(CURRENT_VARIABLES)
        if include_hourly:
            params["hourly"] = ",".join(HOURLY_VARIABLES)

        url = f"{self._base_url}?{urlencode(params)}"
        try:
            if self._client is not None:
                response = self._client.get(url, timeout=self._timeout)
            else:
                with httpx.Client(timeout=self._timeout) as client:
                    response = client.get(url)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise WeatherProviderError(
                f"Open-Meteo request failed for ({location.latitude}, {location.longitude})",
                cause=exc,
            ) from exc
        except ValueError as exc:
            raise WeatherProviderError("Open-Meteo returned invalid JSON", cause=exc) from exc

    def _normalize_current(self, payload: dict[str, Any]) -> NormalizedCurrentWeather:
        current = payload.get("current")
        if not isinstance(current, dict):
            raise WeatherProviderError("Open-Meteo response missing current block")

        fetched_at = datetime.now(timezone.utc)
        time_raw = current.get("time")
        if not isinstance(time_raw, str):
            raise WeatherProviderError("Open-Meteo current.time missing")

        return NormalizedCurrentWeather(
            observed_at=_parse_provider_time(time_raw),
            timezone=str(payload.get("timezone") or "UTC"),
            latitude=float(payload.get("latitude", 0.0)),
            longitude=float(payload.get("longitude", 0.0)),
            temperature_c=_require_number(current, "temperature_2m"),
            relative_humidity_pct=_require_number(current, "relative_humidity_2m"),
            precipitation_mm=_require_number(current, "precipitation"),
            wind_speed_kmh=_require_number(current, "wind_speed_10m"),
            wind_direction_deg=_require_number(current, "wind_direction_10m"),
            pressure_hpa=_require_number(current, "surface_pressure"),
            weather_code=int(_require_number(current, "weather_code")),
            provider=self.name,
            provider_fetched_at=fetched_at,
        )

    def _normalize_forecast(
        self,
        payload: dict[str, Any],
        *,
        hours: int,
    ) -> NormalizedWeatherForecast:
        hourly = payload.get("hourly")
        if not isinstance(hourly, dict):
            raise WeatherProviderError("Open-Meteo response missing hourly block")

        times = hourly.get("time")
        if not isinstance(times, list) or not times:
            raise WeatherProviderError("Open-Meteo hourly.time missing")

        limit = max(1, min(hours, len(times)))
        points: list[NormalizedForecastPoint] = []

        for index in range(limit):
            timestamp = _parse_provider_time(str(times[index]))
            points.append(
                NormalizedForecastPoint(
                    timestamp=timestamp,
                    temperature_c=float(hourly["temperature_2m"][index]),
                    relative_humidity_pct=float(hourly["relative_humidity_2m"][index]),
                    precipitation_mm=float(hourly["precipitation"][index]),
                    precipitation_probability_pct=_optional_number_at(hourly, "precipitation_probability", index),
                    wind_speed_kmh=float(hourly["wind_speed_10m"][index]),
                    wind_gusts_kmh=_optional_number_at(hourly, "wind_gusts_10m", index),
                    pressure_hpa=float(hourly["surface_pressure"][index]),
                    weather_code=int(hourly["weather_code"][index]),
                )
            )

        fetched_at = datetime.now(timezone.utc)
        return NormalizedWeatherForecast(
            timezone=str(payload.get("timezone") or "UTC"),
            latitude=float(payload.get("latitude", 0.0)),
            longitude=float(payload.get("longitude", 0.0)),
            hourly=points,
            provider=self.name,
            provider_fetched_at=fetched_at,
        )


def _optional_number_at(payload: dict[str, Any], key: str, index: int) -> float | None:
    series = payload.get(key)
    if not isinstance(series, list) or index >= len(series):
        return None
    raw = series[index]
    if raw is None:
        return None
    return float(raw)
