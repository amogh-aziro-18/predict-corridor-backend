"""Environment intelligence API — weather (8A) + signals (9B) + impact (9C)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.common import MessageResponse
from app.api.schemas.environment_impact import EnvironmentImpactAssessmentOut
from app.api.schemas.environment_signal import EnvironmentObservationSet
from app.api.schemas.weather import CurrentWeatherOut, WeatherForecastOut
from app.api.schemas.weather_impact import WeatherImpactAssessmentOut
from app.services.environment_impact_service import EnvironmentImpactService
from app.services.environment_service import EnvironmentService
from app.services.weather_impact_service import WeatherImpactService
from app.services.weather_service import WeatherService

router = APIRouter()


@router.get("/environment", response_model=MessageResponse)
def get_environment_summary() -> MessageResponse:
    """Discovery — live weather, air-quality signals, and environmental impact."""
    return MessageResponse(
        message=(
            "Environment: weather at /environment/weather/*; "
            "air-quality signals at /environment/signals; "
            "environmental impact at /environment/impact."
        )
    )


@router.get("/environment/signals", response_model=EnvironmentObservationSet)
def get_environment_signals(
    zone: str | None = Query(default=None, description="Optional zone id (e.g. zone-c)"),
    utility_kind: str | None = Query(
        default=None,
        description=(
            "Optional utility context for asset-centroid location resolution. "
            "Defaults to first ENABLED_UTILITIES kind when omitted."
        ),
    ),
    hours: int = Query(
        default=24,
        ge=0,
        le=168,
        description="Forecast horizon in hours (0 = current observations only).",
    ),
    db: Session = Depends(db_dep),
) -> EnvironmentObservationSet:
    """
    Normalized air-quality EnvironmentSignal observations (Phase 9B).

    Uses Open-Meteo Air Quality at the asset-centroid location. Does not fabricate
    values. Weather remains on ``/environment/weather/*``.
    """
    return EnvironmentService(db).get_observation_set(
        zone_id=zone,
        utility_kind=utility_kind,
        hours=hours,
    )


@router.get("/environment/impact", response_model=EnvironmentImpactAssessmentOut)
def get_environment_impact(
    zone: str | None = Query(default=None, description="Optional zone id (e.g. zone-c)"),
    utility_kind: str | None = Query(
        default=None,
        description=(
            "Optional utility context for asset-centroid location resolution. "
            "Defaults to first ENABLED_UTILITIES kind when omitted."
        ),
    ),
    hours: int = Query(
        default=24,
        ge=0,
        le=168,
        description="Forecast horizon in hours (0 = current observations only).",
    ),
    db: Session = Depends(db_dep),
) -> EnvironmentImpactAssessmentOut:
    """
    Deterministic environmental impact assessment (Phase 9C).

    Consumes EnvironmentSignal observations via EnvironmentService. US AQI is
    assessed only when ``metadata.aqi_standard == us_aqi``. Corridor/zone scope
    only — no fabricated asset-level impact.
    """
    return EnvironmentImpactService(db).assess(
        zone_id=zone,
        utility_kind=utility_kind,
        hours=hours,
    )


@router.get("/environment/weather/current", response_model=CurrentWeatherOut)
def get_environment_weather_current(
    zone: str | None = Query(default=None, description="Optional zone id (e.g. zone-c)"),
    db: Session = Depends(db_dep),
) -> CurrentWeatherOut:
    """Current weather from external provider (normalized corridor/zone location)."""
    return WeatherService(db).get_current(zone_id=zone)


@router.get("/environment/weather/forecast", response_model=WeatherForecastOut)
def get_environment_weather_forecast(
    zone: str | None = Query(default=None, description="Optional zone id (e.g. zone-c)"),
    hours: int = Query(default=48, ge=1, le=168, description="Hourly forecast horizon"),
    db: Session = Depends(db_dep),
) -> WeatherForecastOut:
    """Hourly weather forecast from external provider."""
    return WeatherService(db).get_forecast(zone_id=zone, hours=hours)


@router.get("/environment/weather/impact", response_model=WeatherImpactAssessmentOut)
def get_environment_weather_impact(
    zone: str | None = Query(default=None, description="Optional zone id (e.g. zone-c)"),
    hours: int = Query(default=48, ge=1, le=168, description="Forecast horizon for impact rules"),
    db: Session = Depends(db_dep),
) -> WeatherImpactAssessmentOut:
    """
    Deterministic weather impact assessment from normalized current + forecast data.

    Evaluates corridor or zone centroid weather only — not per-asset measurements.
    Also syncs persisted weather alerts (source=weather) via set-diff against
    threshold breaches for this scope — no work orders, no telemetry mutation.
    """
    return WeatherImpactService(db).assess(zone_id=zone, hours=hours)
