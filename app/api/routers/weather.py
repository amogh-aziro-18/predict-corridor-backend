"""Weather API — normalized external provider data (Phase 8A)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.weather import CurrentWeatherOut, WeatherForecastOut
from app.services.weather_service import WeatherService

router = APIRouter()


@router.get("/weather/current", response_model=CurrentWeatherOut)
def get_current_weather(
    zone: str | None = Query(default=None, description="Optional zone id (e.g. zone-c)"),
    db: Session = Depends(db_dep),
) -> CurrentWeatherOut:
    """Current conditions from configured external weather provider."""
    return WeatherService(db).get_current(zone_id=zone)


@router.get("/weather/forecast", response_model=WeatherForecastOut)
def get_weather_forecast(
    zone: str | None = Query(default=None, description="Optional zone id (e.g. zone-c)"),
    hours: int = Query(default=48, ge=1, le=168, description="Hourly forecast horizon"),
    db: Session = Depends(db_dep),
) -> WeatherForecastOut:
    """Hourly forecast from configured external weather provider."""
    return WeatherService(db).get_forecast(zone_id=zone, hours=hours)
