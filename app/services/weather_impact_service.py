"""
Deterministic Weather Impact Assessment (Phase 8C).

Consumes normalized data from WeatherService only — never calls external providers
directly. Produces typed WeatherImpactSignalOut objects when explicit thresholds
are breached.

Limitations (documented):
- Weather is evaluated at corridor/zone asset centroids, not per-asset.
- ``weather_exposure`` and ``flood_risk`` on assets are static seed metadata only.
- No probabilistic flood or failure models.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.api.schemas.weather import CurrentWeatherOut, ForecastPointOut, WeatherForecastOut
from app.api.schemas.weather_impact import (
    StaticAssetRiskMetadataOut,
    WeatherImpactAssessmentOut,
    WeatherImpactObservationKind,
    WeatherImpactScope,
    WeatherImpactSignalOut,
    WeatherImpactSignalType,
)
from app.core.config import get_settings
from app.domain.repositories.asset_repository import AssetRepository
from app.services.weather_impact_thresholds import (
    SEVERITY_RANK,
    classify_humidity,
    classify_precipitation,
    classify_pressure,
    classify_temperature,
    classify_weather_code,
    classify_wind_gust,
    classify_wind_speed,
    max_severity,
)
from app.services.weather_service import WeatherService

_STATIC_METADATA_NOTE = (
    "Static seed/catalog metadata from the asset registry across enabled live "
    "utilities (Water/Power/Gas/Communications when enabled). "
    "Not live weather risk, flood probability, or weather-model output. "
    "Weather does not mutate simulator telemetry."
)


class WeatherImpactService:
    """Rule-based weather impact assessment over WeatherService data."""

    def __init__(self, db: Session, *, weather_service: WeatherService | None = None) -> None:
        self._db = db
        self._weather = weather_service or WeatherService(db)
        self._assets = AssetRepository(db)

    def assess(
        self,
        *,
        zone_id: str | None = None,
        hours: int = 48,
    ) -> WeatherImpactAssessmentOut:
        """
        Assess weather impact for corridor or zone scope.

        Raises WeatherUnavailableError when WeatherService cannot supply data.
        """
        hours = max(1, min(hours, 168))
        current = self._weather.get_current(zone_id=zone_id)
        forecast = self._weather.get_forecast(zone_id=zone_id, hours=hours)

        scope = (
            WeatherImpactScope.ZONE
            if current.location.scope == "zone"
            else WeatherImpactScope.CORRIDOR
        )
        signals: list[WeatherImpactSignalOut] = []
        signals.extend(self._signals_from_current(current, scope=scope, zone_id=zone_id))
        signals.extend(
            self._signals_from_forecast(
                forecast,
                scope=scope,
                zone_id=zone_id,
            )
        )

        assessed_at = datetime.now(timezone.utc)
        overall = max_severity([s.severity for s in signals])

        assessment = WeatherImpactAssessmentOut(
            location=current.location,
            provider_meta=current.provider_meta,
            forecast_provider_meta=forecast.provider_meta,
            assessed_at=assessed_at,
            scope=scope,
            zone_id=zone_id,
            forecast_hours=hours,
            overall_severity=overall,
            signals=signals,
            static_metadata_context=self._static_metadata_context(zone_id=zone_id),
        )

        # Phase 03: persist weather-origin Alerts (set-diff). Does not create WOs.
        from app.services.weather_alert_producer import sync_weather_alerts_from_assessment

        sync_weather_alerts_from_assessment(self._db, assessment)

        return assessment

    def _static_metadata_context(
        self,
        *,
        zone_id: str | None,
    ) -> StaticAssetRiskMetadataOut:
        """
        Aggregate static weather_exposure / flood_risk labels for all enabled
        live utilities in scope (Water/Power/Gas/Communications).

        Centroid weather signals remain utility-agnostic. This metadata is
        planning context only — never used to mutate telemetry.
        """
        enabled = get_settings().enabled_utility_kinds
        assets = []
        for kind in enabled:
            assets.extend(self._assets.list(utility_kind=kind, zone_id=zone_id))
        exposure = Counter(a.weather_exposure for a in assets if a.weather_exposure)
        flood = Counter(a.flood_risk for a in assets if a.flood_risk)
        return StaticAssetRiskMetadataOut(
            note=_STATIC_METADATA_NOTE,
            utility_kind="all_enabled",
            zone_id=zone_id,
            asset_count=len(assets),
            weather_exposure=dict(exposure),
            flood_risk=dict(flood),
        )

    def _signals_from_current(
        self,
        current: CurrentWeatherOut,
        *,
        scope: WeatherImpactScope,
        zone_id: str | None,
    ) -> list[WeatherImpactSignalOut]:
        source = current.provider_meta.provider
        observed_at = current.observed_at
        base_kwargs = {
            "scope": scope,
            "zone_id": zone_id,
            "observation_kind": WeatherImpactObservationKind.CURRENT,
            "observed_at": observed_at,
            "source": source,
            "affected_asset_ids": [],
        }

        signals: list[WeatherImpactSignalOut] = []
        mapping: list[tuple[WeatherImpactSignalType, float, str, object]] = [
            (WeatherImpactSignalType.PRECIPITATION, current.precipitation_mm, "mm", classify_precipitation),
            (WeatherImpactSignalType.WIND_SPEED, current.wind_speed_kmh, "km/h", classify_wind_speed),
            (WeatherImpactSignalType.TEMPERATURE, current.temperature_c, "°C", classify_temperature),
            (
                WeatherImpactSignalType.RELATIVE_HUMIDITY,
                current.relative_humidity_pct,
                "%",
                classify_humidity,
            ),
            (WeatherImpactSignalType.PRESSURE, current.pressure_hpa, "hPa", classify_pressure),
            (
                WeatherImpactSignalType.WEATHER_CODE,
                float(current.weather_code),
                "code",
                classify_weather_code,
            ),
        ]
        for signal_type, value, unit, classifier in mapping:
            breach = classifier(value) if signal_type != WeatherImpactSignalType.WEATHER_CODE else classifier(int(value))
            if breach is None:
                continue
            signals.append(
                WeatherImpactSignalOut(
                    signal_type=signal_type,
                    severity=breach.severity,
                    measured_value=value,
                    unit=unit,
                    threshold_rule=breach.threshold_rule,
                    explanation=breach.explanation,
                    **base_kwargs,
                )
            )
        return signals

    def _signals_from_forecast(
        self,
        forecast: WeatherForecastOut,
        *,
        scope: WeatherImpactScope,
        zone_id: str | None,
    ) -> list[WeatherImpactSignalOut]:
        if not forecast.hourly:
            return []

        source = forecast.provider_meta.provider
        signals: list[WeatherImpactSignalOut] = []

        precip_peak = self._peak_point(forecast.hourly, key="precipitation_mm")
        if precip_peak:
            breach = classify_precipitation(precip_peak.precipitation_mm)
            if breach:
                signals.append(
                    self._forecast_signal(
                        signal_type=WeatherImpactSignalType.PRECIPITATION,
                        breach=breach,
                        value=precip_peak.precipitation_mm,
                        unit="mm",
                        point=precip_peak,
                        scope=scope,
                        zone_id=zone_id,
                        source=source,
                        detail="peak forecast hour",
                    )
                )

        wind_peak = self._peak_point(forecast.hourly, key="wind_speed_kmh")
        if wind_peak:
            breach = classify_wind_speed(wind_peak.wind_speed_kmh)
            if breach:
                signals.append(
                    self._forecast_signal(
                        signal_type=WeatherImpactSignalType.WIND_SPEED,
                        breach=breach,
                        value=wind_peak.wind_speed_kmh,
                        unit="km/h",
                        point=wind_peak,
                        scope=scope,
                        zone_id=zone_id,
                        source=source,
                        detail="peak forecast hour",
                    )
                )

        gust_points = [p for p in forecast.hourly if p.wind_gusts_kmh is not None]
        if gust_points:
            gust_peak = max(gust_points, key=lambda p: p.wind_gusts_kmh or 0.0)
            if gust_peak.wind_gusts_kmh is not None:
                breach = classify_wind_gust(gust_peak.wind_gusts_kmh)
                if breach:
                    signals.append(
                        self._forecast_signal(
                            signal_type=WeatherImpactSignalType.WIND_GUST,
                            breach=breach,
                            value=gust_peak.wind_gusts_kmh,
                            unit="km/h",
                            point=gust_peak,
                            scope=scope,
                            zone_id=zone_id,
                            source=source,
                            detail="peak forecast gust",
                        )
                    )

        temp_hot = max(forecast.hourly, key=lambda p: p.temperature_c)
        breach_hot = classify_temperature(temp_hot.temperature_c)
        if breach_hot and temp_hot.temperature_c >= 35.0:
            signals.append(
                self._forecast_signal(
                    signal_type=WeatherImpactSignalType.TEMPERATURE,
                    breach=breach_hot,
                    value=temp_hot.temperature_c,
                    unit="°C",
                    point=temp_hot,
                    scope=scope,
                    zone_id=zone_id,
                    source=source,
                    detail="peak forecast temperature",
                )
            )

        temp_cold = min(forecast.hourly, key=lambda p: p.temperature_c)
        breach_cold = classify_temperature(temp_cold.temperature_c)
        if breach_cold and temp_cold.temperature_c <= 5.0:
            signals.append(
                self._forecast_signal(
                    signal_type=WeatherImpactSignalType.TEMPERATURE,
                    breach=breach_cold,
                    value=temp_cold.temperature_c,
                    unit="°C",
                    point=temp_cold,
                    scope=scope,
                    zone_id=zone_id,
                    source=source,
                    detail="lowest forecast temperature",
                )
            )

        adverse_codes = [
            p
            for p in forecast.hourly
            if classify_weather_code(p.weather_code) is not None
        ]
        if adverse_codes:
            worst = max(
                adverse_codes,
                key=lambda p: SEVERITY_RANK.get(
                    classify_weather_code(p.weather_code).severity,  # type: ignore[union-attr]
                    0,
                ),
            )
            breach = classify_weather_code(worst.weather_code)
            if breach:
                signals.append(
                    self._forecast_signal(
                        signal_type=WeatherImpactSignalType.WEATHER_CODE,
                        breach=breach,
                        value=float(worst.weather_code),
                        unit="code",
                        point=worst,
                        scope=scope,
                        zone_id=zone_id,
                        source=source,
                        detail="most adverse forecast hour",
                    )
                )

        return signals

    @staticmethod
    def _peak_point(
        hourly: list[ForecastPointOut],
        *,
        key: str,
    ) -> ForecastPointOut | None:
        if not hourly:
            return None
        return max(hourly, key=lambda p: getattr(p, key))

    @staticmethod
    def _forecast_signal(
        *,
        signal_type: WeatherImpactSignalType,
        breach,
        value: float,
        unit: str,
        point: ForecastPointOut,
        scope: WeatherImpactScope,
        zone_id: str | None,
        source: str,
        detail: str,
    ) -> WeatherImpactSignalOut:
        return WeatherImpactSignalOut(
            signal_type=signal_type,
            severity=breach.severity,
            scope=scope,
            zone_id=zone_id,
            affected_asset_ids=[],
            observation_kind=WeatherImpactObservationKind.FORECAST,
            measured_value=value,
            unit=unit,
            threshold_rule=breach.threshold_rule,
            explanation=f"{breach.explanation} ({detail} at forecast time).",
            observed_at=point.timestamp,
            source=source,
        )
