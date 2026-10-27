"""
Environment observation facade (Phase 9A foundation + Phase 9B live air quality).

Retrieves real EnvironmentSignal observations via EnvironmentProvider.
Does not assess impact, generate recommendations, or route weather.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.api.schemas.environment_signal import (
    EnvironmentObservationKind,
    EnvironmentObservationSet,
    EnvironmentSignal,
    EnvironmentSignalCategory,
    EnvironmentSignalScope,
)
from app.api.schemas.weather import WeatherLocationOut, WeatherProviderMetaOut
from app.core.config import Settings, get_settings
from app.core.errors import EnvironmentUnavailableError, ValidationAppError
from app.integrations.environment.base import (
    EnvironmentLocation,
    EnvironmentProvider,
    EnvironmentProviderError,
    NormalizedEnvironmentObservation,
)
from app.integrations.environment.cache import (
    get_environment_observation_cache,
)
from app.integrations.environment.location import resolve_environment_location
from app.integrations.environment.open_meteo_air_quality import OpenMeteoAirQualityProvider
from app.integrations.weather.cache import TtlCache


@dataclass(frozen=True)
class _CachedObservationBundle:
    location: EnvironmentLocation
    timezone: str
    observations: list[NormalizedEnvironmentObservation]
    fetched_at: datetime


def build_environment_provider(settings: Settings | None = None) -> EnvironmentProvider:
    """Factory for configured environment observation provider."""
    cfg = settings or get_settings()
    provider_key = cfg.environment_provider.strip().lower()
    if provider_key == "open_meteo_air_quality":
        return OpenMeteoAirQualityProvider(
            base_url=cfg.open_meteo_air_quality_base_url,
            timeout_seconds=cfg.environment_http_timeout_seconds,
        )
    raise EnvironmentUnavailableError(f"Unsupported environment provider: {provider_key}")


class EnvironmentService:
    """
    Environment observation facade.

    Phase 9B: Open-Meteo Air Quality → EnvironmentSignal.
    Does not invent AQI, assess thresholds, or call WeatherService.
    """

    def __init__(
        self,
        db: Session | None = None,
        *,
        provider: EnvironmentProvider | None = None,
        settings: Settings | None = None,
        cache: TtlCache | None = None,
    ) -> None:
        self._db = db
        self._settings = settings or get_settings()
        self._provider = provider or build_environment_provider(self._settings)
        self._cache = cache or get_environment_observation_cache()

    def normalize_signal(self, payload: EnvironmentSignal | dict) -> EnvironmentSignal:
        """Validate and return a typed EnvironmentSignal."""
        if isinstance(payload, EnvironmentSignal):
            return EnvironmentSignal.model_validate(payload.model_dump())
        return EnvironmentSignal.model_validate(payload)

    def get_observation_set(
        self,
        *,
        zone_id: str | None = None,
        utility_kind: str | None = None,
        hours: int = 24,
    ) -> EnvironmentObservationSet:
        """
        Return air-quality EnvironmentSignal observations for a scope.

        Raises EnvironmentUnavailableError when the provider fails and no stale
        cache is available. Never fabricates AQI/pollutant values.
        """
        if self._db is None:
            raise EnvironmentUnavailableError(
                "Database session required to resolve environment location from assets"
            )

        hours = max(0, min(int(hours), 168))
        resolved_kind = self._resolve_utility_kind(utility_kind)
        location = resolve_environment_location(
            self._db,
            zone_id=zone_id,
            utility_kind=resolved_kind,
        )
        cache_key = self._cache_key(location.scope, location.zone_id, location.utility_kind, hours)

        cached = self._cache.get(cache_key)
        now = datetime.now(timezone.utc)

        if cached and cached.is_fresh(now):
            bundle: _CachedObservationBundle = cached.value
            return self._to_observation_set(
                bundle,
                cache_status="cached",
                cache_expires_at=cached.expires_at,
                stale=False,
            )

        try:
            observations = self._provider.fetch_observations(
                location,
                observation_kind="all" if hours > 0 else "current",
                hours=hours,
            )
            timezone_name = location.timezone or "UTC"
            if observations:
                tz_meta = observations[0].metadata.get("provider_timezone")
                if isinstance(tz_meta, str) and tz_meta.strip():
                    timezone_name = tz_meta
            fetched_at = datetime.now(timezone.utc)
            if observations:
                fetched_at = observations[0].provider_fetched_at
            bundle = _CachedObservationBundle(
                location=location,
                timezone=timezone_name,
                observations=list(observations),
                fetched_at=fetched_at,
            )
            self._cache.set(
                cache_key,
                bundle,
                ttl_seconds=self._settings.environment_cache_ttl_seconds,
                fetched_at=fetched_at,
            )
            entry = self._cache.get(cache_key)
            return self._to_observation_set(
                bundle,
                cache_status="fresh",
                cache_expires_at=entry.expires_at if entry else None,
                stale=False,
            )
        except EnvironmentProviderError as exc:
            if cached is not None:
                bundle = cached.value
                return self._to_observation_set(
                    bundle,
                    cache_status="stale",
                    cache_expires_at=cached.expires_at,
                    stale=True,
                )
            raise EnvironmentUnavailableError(str(exc.message)) from exc

    def list_signals(
        self,
        *,
        zone_id: str | None = None,
        utility_kind: str | None = None,
        hours: int = 24,
    ) -> list[EnvironmentSignal]:
        return list(
            self.get_observation_set(
                zone_id=zone_id,
                utility_kind=utility_kind,
                hours=hours,
            ).signals
        )

    def summary(self) -> dict:
        return {
            "phase": "9B",
            "provider_configured": True,
            "provider": self._provider.name,
            "note": (
                "Live air-quality observations via EnvironmentProvider. "
                "Weather remains on /environment/weather/*."
            ),
        }

    def _resolve_utility_kind(self, utility_kind: str | None) -> str:
        if utility_kind and utility_kind.strip():
            return utility_kind.strip().lower()
        kinds = self._settings.enabled_utility_kinds
        if not kinds:
            raise ValidationAppError(
                "No utility kinds enabled for environment location resolution"
            )
        # Deployment default from ENABLED_UTILITIES — not a hardcoded water literal
        # inside the provider. Today typically ["water"].
        return kinds[0]

    @staticmethod
    def _cache_key(scope: str, zone_id: str | None, utility_kind: str, hours: int) -> str:
        zone_part = zone_id or "all"
        return f"env:{scope}:{zone_part}:{utility_kind}:{hours}"

    def _to_observation_set(
        self,
        bundle: _CachedObservationBundle,
        *,
        cache_status: str,
        cache_expires_at: datetime | None,
        stale: bool,
    ) -> EnvironmentObservationSet:
        loc = bundle.location
        location_out = WeatherLocationOut(
            latitude=loc.latitude,
            longitude=loc.longitude,
            timezone=bundle.timezone,
            scope=loc.scope,
            zone_id=loc.zone_id,
            asset_count=loc.asset_count,
            utility_kind=loc.utility_kind,
            method=loc.method,
            city=getattr(loc, "city", None),
            country=getattr(loc, "country", None),
            display_name=getattr(loc, "display_name", None),
            location_note=getattr(loc, "location_note", None),
        )
        provider_meta = WeatherProviderMetaOut(
            provider=self._provider.name,
            fetched_at=bundle.fetched_at,
            cache_status=cache_status,
            cache_expires_at=cache_expires_at,
            stale=stale,
        )
        scope = (
            EnvironmentSignalScope.ZONE
            if loc.scope == "zone"
            else EnvironmentSignalScope.CORRIDOR
        )
        signals = [
            self._observation_to_signal(
                obs,
                location_out=location_out,
                provider_meta=provider_meta,
                scope=scope,
                zone_id=loc.zone_id,
                utility_kind=loc.utility_kind,
            )
            for obs in bundle.observations
        ]
        current_times = [
            s.observed_at
            for s in signals
            if s.observation_kind == EnvironmentObservationKind.CURRENT
        ]
        observed_at = max(current_times) if current_times else (
            max((s.observed_at for s in signals), default=None) if signals else None
        )
        note = None
        if stale:
            note = (
                "Returning stale cached air-quality observations after provider failure. "
                "Values are not fabricated."
            )
        return EnvironmentObservationSet(
            scope=scope,
            zone_id=loc.zone_id,
            utility_kind=loc.utility_kind,
            location=location_out,
            provider_meta=provider_meta,
            observed_at=observed_at,
            generated_at=datetime.now(timezone.utc),
            signals=signals,
            note=note,
        )

    def _observation_to_signal(
        self,
        obs: NormalizedEnvironmentObservation,
        *,
        location_out: WeatherLocationOut,
        provider_meta: WeatherProviderMetaOut,
        scope: EnvironmentSignalScope,
        zone_id: str | None,
        utility_kind: str,
    ) -> EnvironmentSignal:
        kind = EnvironmentObservationKind(obs.observation_kind)
        metadata = dict(obs.metadata)
        unit = obs.unit
        # Phase 9B.1 — never present Open-Meteo US AQI as generic/CPCB/Indian AQI.
        if obs.signal_type == "aqi":
            unit = "US AQI"
            metadata["aqi_standard"] = "us_aqi"
            metadata.setdefault("aqi_index", "United States Air Quality Index")
            metadata.setdefault("provider_field", "us_aqi")
            metadata.setdefault(
                "aqi_semantics",
                (
                    "Provider-supplied US AQI (Open-Meteo us_aqi). "
                    "Not CPCB AQI and not Indian National AQI."
                ),
            )
        return EnvironmentSignal(
            category=EnvironmentSignalCategory.AIR_QUALITY,
            signal_type=obs.signal_type,
            measured_value=obs.measured_value,
            unit=unit,
            observation_kind=kind,
            observed_at=obs.observed_at,
            source=obs.provider,
            scope=scope,
            zone_id=zone_id,
            utility_kind=utility_kind,
            location=location_out,
            provider_meta=provider_meta,
            confidence=obs.confidence,
            metadata=metadata,
        )
