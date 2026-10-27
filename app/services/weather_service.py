"""Weather orchestration — location resolution, provider adapter, TTL cache."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.api.schemas.weather import (
    CurrentWeatherOut,
    ForecastPointOut,
    WeatherForecastOut,
    WeatherLocationOut,
    WeatherProviderMetaOut,
)
from app.core.config import Settings, get_settings
from app.core.errors import WeatherUnavailableError
from app.integrations.weather.base import (
    NormalizedCurrentWeather,
    NormalizedWeatherForecast,
    WeatherProvider,
    WeatherProviderError,
)
from app.integrations.weather.cache import (
    TtlCache,
    get_current_weather_cache,
    get_forecast_weather_cache,
)
from app.integrations.weather.location import resolve_weather_location
from app.integrations.weather.open_meteo import OpenMeteoWeatherProvider


def build_weather_provider(settings: Settings | None = None) -> WeatherProvider:
    """Factory for configured weather provider (Open-Meteo default)."""
    cfg = settings or get_settings()
    provider_key = cfg.weather_provider.strip().lower()
    if provider_key == "open_meteo":
        return OpenMeteoWeatherProvider(
            base_url=cfg.open_meteo_base_url,
            timeout_seconds=cfg.weather_http_timeout_seconds,
        )
    raise WeatherUnavailableError(f"Unsupported weather provider: {provider_key}")


class WeatherService:
    """Fetch normalized weather via provider adapter with in-memory TTL cache."""

    def __init__(
        self,
        db: Session,
        *,
        provider: WeatherProvider | None = None,
        settings: Settings | None = None,
        current_cache: TtlCache | None = None,
        forecast_cache: TtlCache | None = None,
    ) -> None:
        self._db = db
        self._settings = settings or get_settings()
        self._provider = provider or build_weather_provider(self._settings)
        self._current_cache = current_cache or get_current_weather_cache()
        self._forecast_cache = forecast_cache or get_forecast_weather_cache()

    def get_current(self, *, zone_id: str | None = None) -> CurrentWeatherOut:
        location = resolve_weather_location(self._db, zone_id=zone_id)
        cache_key = self._cache_key("current", location.scope, location.zone_id)

        cached = self._current_cache.get(cache_key)
        now = datetime.now(timezone.utc)
        if cached and cached.is_fresh(now):
            return self._to_current_out(
                cached.value,
                location=location,
                cache_status="cached",
                cache_expires_at=cached.expires_at,
                stale=False,
            )

        try:
            normalized = self._provider.fetch_current(location)
            self._current_cache.set(
                cache_key,
                normalized,
                ttl_seconds=self._settings.weather_cache_ttl_seconds,
            )
            entry = self._current_cache.get(cache_key)
            return self._to_current_out(
                normalized,
                location=location,
                cache_status="fresh",
                cache_expires_at=entry.expires_at if entry else None,
                stale=False,
            )
        except WeatherProviderError as exc:
            if cached is not None:
                return self._to_current_out(
                    cached.value,
                    location=location,
                    cache_status="stale",
                    cache_expires_at=cached.expires_at,
                    stale=True,
                )
            raise WeatherUnavailableError(str(exc.message)) from exc

    def get_forecast(self, *, zone_id: str | None = None, hours: int = 48) -> WeatherForecastOut:
        hours = max(1, min(hours, 168))
        location = resolve_weather_location(self._db, zone_id=zone_id)
        cache_key = self._cache_key("forecast", location.scope, location.zone_id, str(hours))

        cached = self._forecast_cache.get(cache_key)
        now = datetime.now(timezone.utc)
        if cached and cached.is_fresh(now):
            return self._to_forecast_out(
                cached.value,
                location=location,
                cache_status="cached",
                cache_expires_at=cached.expires_at,
                stale=False,
            )

        try:
            normalized = self._provider.fetch_forecast(location, hours=hours)
            self._forecast_cache.set(
                cache_key,
                normalized,
                ttl_seconds=self._settings.weather_forecast_cache_ttl_seconds,
            )
            entry = self._forecast_cache.get(cache_key)
            return self._to_forecast_out(
                normalized,
                location=location,
                cache_status="fresh",
                cache_expires_at=entry.expires_at if entry else None,
                stale=False,
            )
        except WeatherProviderError as exc:
            if cached is not None:
                return self._to_forecast_out(
                    cached.value,
                    location=location,
                    cache_status="stale",
                    cache_expires_at=cached.expires_at,
                    stale=True,
                )
            raise WeatherUnavailableError(str(exc.message)) from exc

    @staticmethod
    def _cache_key(kind: str, scope: str, zone_id: str | None, *extra: str) -> str:
        zone_part = zone_id or "all"
        suffix = ":".join(extra) if extra else ""
        if suffix:
            return f"{kind}:{scope}:{zone_part}:{suffix}"
        return f"{kind}:{scope}:{zone_part}"

    @staticmethod
    def _location_out(location, timezone: str) -> WeatherLocationOut:
        return WeatherLocationOut(
            latitude=location.latitude,
            longitude=location.longitude,
            timezone=timezone,
            scope=location.scope,
            zone_id=location.zone_id,
            asset_count=location.asset_count,
            utility_kind=location.utility_kind,
            method=location.method,
            city=getattr(location, "city", None),
            country=getattr(location, "country", None),
            display_name=getattr(location, "display_name", None),
            location_note=getattr(location, "location_note", None),
        )

    def _to_current_out(
        self,
        normalized: NormalizedCurrentWeather,
        *,
        location,
        cache_status: str,
        cache_expires_at: datetime | None,
        stale: bool,
    ) -> CurrentWeatherOut:
        return CurrentWeatherOut(
            location=self._location_out(location, normalized.timezone),
            provider_meta=WeatherProviderMetaOut(
                provider=normalized.provider,
                fetched_at=normalized.provider_fetched_at,
                cache_status=cache_status,
                cache_expires_at=cache_expires_at,
                stale=stale,
            ),
            observed_at=normalized.observed_at,
            temperature_c=normalized.temperature_c,
            relative_humidity_pct=normalized.relative_humidity_pct,
            precipitation_mm=normalized.precipitation_mm,
            wind_speed_kmh=normalized.wind_speed_kmh,
            wind_direction_deg=normalized.wind_direction_deg,
            pressure_hpa=normalized.pressure_hpa,
            weather_code=normalized.weather_code,
        )

    def _to_forecast_out(
        self,
        normalized: NormalizedWeatherForecast,
        *,
        location,
        cache_status: str,
        cache_expires_at: datetime | None,
        stale: bool,
    ) -> WeatherForecastOut:
        hourly = [
            ForecastPointOut(
                timestamp=point.timestamp,
                temperature_c=point.temperature_c,
                relative_humidity_pct=point.relative_humidity_pct,
                precipitation_mm=point.precipitation_mm,
                precipitation_probability_pct=point.precipitation_probability_pct,
                wind_speed_kmh=point.wind_speed_kmh,
                wind_gusts_kmh=point.wind_gusts_kmh,
                pressure_hpa=point.pressure_hpa,
                weather_code=point.weather_code,
            )
            for point in normalized.hourly
        ]
        return WeatherForecastOut(
            location=self._location_out(location, normalized.timezone),
            provider_meta=WeatherProviderMetaOut(
                provider=normalized.provider,
                fetched_at=normalized.provider_fetched_at,
                cache_status=cache_status,
                cache_expires_at=cache_expires_at,
                stale=stale,
            ),
            hourly=hourly,
        )
