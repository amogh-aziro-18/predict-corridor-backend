"""
Open-Meteo Air Quality API adapter (Phase 9B).

Official contract: https://open-meteo.com/en/docs/air-quality-api
Endpoint: GET https://air-quality-api.open-meteo.com/v1/air-quality

Keyless for non-commercial POC use. No invented endpoints, units, or AQI formulas —
uses provider-supplied ``us_aqi`` (United States AQI) and pollutant concentrations.
Never presents US AQI as CPCB or Indian National AQI.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from app.integrations.environment.base import (
    EnvironmentLocation,
    EnvironmentProvider,
    EnvironmentProviderError,
    NormalizedEnvironmentObservation,
)

# Official Open-Meteo Air Quality ``current`` / ``hourly`` variable names.
CURRENT_VARIABLES = (
    "us_aqi",
    "pm2_5",
    "pm10",
    "carbon_monoxide",
    "nitrogen_dioxide",
    "sulphur_dioxide",
    "ozone",
)

HOURLY_VARIABLES = CURRENT_VARIABLES

# Map Open-Meteo field → (EnvironmentSignal signal_type, default unit, metadata extras)
# AQI is ALWAYS provider-supplied Open-Meteo ``us_aqi`` (United States AQI).
# Never present it as generic, CPCB, or Indian National AQI.
_AQI_METADATA: dict[str, Any] = {
    "aqi_standard": "us_aqi",
    "aqi_index": "United States Air Quality Index",
    "provider_field": "us_aqi",
    "aqi_semantics": (
        "Provider-supplied US AQI (Open-Meteo us_aqi). "
        "Not CPCB AQI and not Indian National AQI."
    ),
}

_FIELD_MAP: dict[str, tuple[str, str, dict[str, Any]]] = {
    "us_aqi": (
        "aqi",
        "US AQI",
        _AQI_METADATA,
    ),
    "pm2_5": ("pm25", "μg/m³", {"provider_field": "pm2_5"}),
    "pm10": ("pm10", "μg/m³", {"provider_field": "pm10"}),
    "carbon_monoxide": ("co", "μg/m³", {"provider_field": "carbon_monoxide"}),
    "nitrogen_dioxide": ("no2", "μg/m³", {"provider_field": "nitrogen_dioxide"}),
    "sulphur_dioxide": ("so2", "μg/m³", {"provider_field": "sulphur_dioxide"}),
    "ozone": ("o3", "μg/m³", {"provider_field": "ozone"}),
}


def _parse_provider_time(value: str) -> datetime:
    if value.endswith("Z"):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _optional_number(payload: dict[str, Any], key: str) -> float | None:
    raw = payload.get(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _unit_from(units: dict[str, Any] | None, field: str, default: str) -> str:
    if not isinstance(units, dict):
        return default
    raw = units.get(field)
    if isinstance(raw, str) and raw.strip():
        return raw
    return default


class OpenMeteoAirQualityProvider(EnvironmentProvider):
    """
    Open-Meteo Air Quality API — https://open-meteo.com/en/docs/air-quality-api

    Base URL default: https://air-quality-api.open-meteo.com/v1/air-quality
    """

    def __init__(
        self,
        *,
        base_url: str = "https://air-quality-api.open-meteo.com/v1/air-quality",
        timeout_seconds: float = 15.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._client = client

    @property
    def name(self) -> str:
        return "open_meteo_air_quality"

    def fetch_observations(
        self,
        location: EnvironmentLocation,
        *,
        observation_kind: str = "current",
        hours: int = 0,
    ) -> list[NormalizedEnvironmentObservation]:
        """
        Fetch current and/or hourly forecast air-quality observations.

        ``observation_kind``:
          - ``current`` — current conditions only (ignores hours)
          - ``forecast`` — hourly forecast for ``hours`` steps
          - ``all`` — current + forecast (used by EnvironmentService)
        """
        kind = (observation_kind or "current").strip().lower()
        include_current = kind in ("current", "all")
        include_hourly = kind in ("forecast", "all") and hours > 0

        if not include_current and not include_hourly:
            include_current = True

        forecast_days = 1
        if include_hourly:
            forecast_days = min(7, max(1, (hours + 23) // 24))

        payload = self._request(
            location,
            include_current=include_current,
            include_hourly=include_hourly,
            forecast_days=forecast_days,
        )
        fetched_at = datetime.now(timezone.utc)
        provider_tz = str(payload.get("timezone") or "UTC")
        observations: list[NormalizedEnvironmentObservation] = []

        if include_current:
            observations.extend(
                self._normalize_current(
                    payload, fetched_at=fetched_at, provider_timezone=provider_tz
                )
            )
        if include_hourly:
            observations.extend(
                self._normalize_forecast(
                    payload,
                    hours=hours,
                    fetched_at=fetched_at,
                    provider_timezone=provider_tz,
                )
            )

        if not observations:
            raise EnvironmentProviderError(
                "Open-Meteo Air Quality response contained no usable observations"
            )
        return observations

    def _request(
        self,
        location: EnvironmentLocation,
        *,
        include_current: bool,
        include_hourly: bool,
        forecast_days: int,
    ) -> dict[str, Any]:
        params: dict[str, str | int | float] = {
            "latitude": location.latitude,
            "longitude": location.longitude,
            "timezone": "auto",
            "domains": "auto",
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
            payload = response.json()
        except httpx.HTTPError as exc:
            raise EnvironmentProviderError(
                f"Open-Meteo Air Quality request failed for "
                f"({location.latitude}, {location.longitude})",
                cause=exc,
            ) from exc
        except ValueError as exc:
            raise EnvironmentProviderError(
                "Open-Meteo Air Quality returned invalid JSON",
                cause=exc,
            ) from exc

        if not isinstance(payload, dict):
            raise EnvironmentProviderError("Open-Meteo Air Quality returned non-object JSON")
        if payload.get("error") is True:
            reason = payload.get("reason") or "unknown provider error"
            raise EnvironmentProviderError(f"Open-Meteo Air Quality error: {reason}")
        return payload

    def _normalize_current(
        self,
        payload: dict[str, Any],
        *,
        fetched_at: datetime,
        provider_timezone: str,
    ) -> list[NormalizedEnvironmentObservation]:
        current = payload.get("current")
        if not isinstance(current, dict):
            raise EnvironmentProviderError("Open-Meteo Air Quality response missing current block")

        time_raw = current.get("time")
        if not isinstance(time_raw, str):
            raise EnvironmentProviderError("Open-Meteo Air Quality current.time missing")

        observed_at = _parse_provider_time(time_raw)
        units = payload.get("current_units")
        if not isinstance(units, dict):
            units = {}

        return self._map_fields(
            current,
            units=units,
            observation_kind="current",
            observed_at=observed_at,
            fetched_at=fetched_at,
            provider_timezone=provider_timezone,
        )

    def _normalize_forecast(
        self,
        payload: dict[str, Any],
        *,
        hours: int,
        fetched_at: datetime,
        provider_timezone: str,
    ) -> list[NormalizedEnvironmentObservation]:
        hourly = payload.get("hourly")
        if not isinstance(hourly, dict):
            raise EnvironmentProviderError("Open-Meteo Air Quality response missing hourly block")

        times = hourly.get("time")
        if not isinstance(times, list) or not times:
            raise EnvironmentProviderError("Open-Meteo Air Quality hourly.time missing")

        units = payload.get("hourly_units")
        if not isinstance(units, dict):
            units = {}

        limit = max(1, min(hours, len(times)))
        now = datetime.now(timezone.utc)
        hour_floor = now.replace(minute=0, second=0, microsecond=0)
        observations: list[NormalizedEnvironmentObservation] = []

        for index in range(limit):
            timestamp = _parse_provider_time(str(times[index]))
            point: dict[str, Any] = {}
            for field in _FIELD_MAP:
                series = hourly.get(field)
                if not isinstance(series, list) or index >= len(series):
                    continue
                raw = series[index]
                if raw is None:
                    continue
                point[field] = raw
            if not point:
                continue
            # Forward-looking provider hours only — do not invent values.
            if timestamp < hour_floor:
                continue
            observations.extend(
                self._map_fields(
                    point,
                    units=units,
                    observation_kind="forecast",
                    observed_at=timestamp,
                    fetched_at=fetched_at,
                    provider_timezone=provider_timezone,
                )
            )

        return observations

    def _map_fields(
        self,
        values: dict[str, Any],
        *,
        units: dict[str, Any],
        observation_kind: str,
        observed_at: datetime,
        fetched_at: datetime,
        provider_timezone: str,
    ) -> list[NormalizedEnvironmentObservation]:
        out: list[NormalizedEnvironmentObservation] = []
        for field, (signal_type, default_unit, extra_meta) in _FIELD_MAP.items():
            measured = _optional_number(values, field)
            if measured is None:
                continue
            # Keep US AQI unit explicit — do not collapse to opaque provider labels like "USAQI".
            if field == "us_aqi":
                unit = "US AQI"
            else:
                unit = _unit_from(units, field, default_unit)
            metadata = dict(extra_meta)
            metadata["provider_timezone"] = provider_timezone
            if field == "us_aqi":
                # Hard guarantee: standard identity survives any unit/label variance.
                metadata["aqi_standard"] = "us_aqi"
                metadata["aqi_index"] = "United States Air Quality Index"
                metadata["provider_field"] = "us_aqi"
                metadata["aqi_semantics"] = _AQI_METADATA["aqi_semantics"]
                if isinstance(units.get("us_aqi"), str):
                    metadata["provider_unit_label"] = units["us_aqi"]
            out.append(
                NormalizedEnvironmentObservation(
                    category="air_quality",
                    signal_type=signal_type,
                    measured_value=measured,
                    unit=unit,
                    observation_kind=observation_kind,
                    observed_at=observed_at,
                    provider=self.name,
                    provider_fetched_at=fetched_at,
                    confidence=None,
                    metadata=metadata,
                )
            )
        return out
