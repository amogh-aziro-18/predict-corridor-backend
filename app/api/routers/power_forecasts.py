"""Power load forecast API — Prophet over Power PostgreSQL telemetry."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.power_forecast import (
    PowerForecastModelStatusOut,
    PowerForecastTrainOut,
    PowerLoadForecastOut,
)
from app.services.power_forecast_service import PowerForecastService

router = APIRouter()


@router.get(
    "/predictions/power/forecast/status",
    response_model=PowerForecastModelStatusOut,
)
def power_forecast_status(db: Session = Depends(db_dep)) -> PowerForecastModelStatusOut:
    payload = PowerForecastService(db).status()
    return PowerForecastModelStatusOut.model_validate(payload)


@router.post(
    "/predictions/power/forecast/train",
    response_model=PowerForecastTrainOut,
)
def train_power_forecast(db: Session = Depends(db_dep)) -> PowerForecastTrainOut:
    result = PowerForecastService(db).train()
    return PowerForecastTrainOut.model_validate(result)


@router.get(
    "/predictions/power/forecast",
    response_model=PowerLoadForecastOut,
)
def power_load_forecast(db: Session = Depends(db_dep)) -> PowerLoadForecastOut:
    """Corridor Power load forecast (active_power_mw sum)."""
    payload = PowerForecastService(db).load_forecast()
    return PowerLoadForecastOut.model_validate(payload)


@router.get(
    "/predictions/power/forecast/zones/{zone_id}/status",
    response_model=PowerForecastModelStatusOut,
)
def zone_power_forecast_status(
    zone_id: str, db: Session = Depends(db_dep)
) -> PowerForecastModelStatusOut:
    payload = PowerForecastService(db).status(zone_id=zone_id)
    return PowerForecastModelStatusOut.model_validate(payload)


@router.post(
    "/predictions/power/forecast/zones/{zone_id}/train",
    response_model=PowerForecastTrainOut,
)
def train_zone_power_forecast(
    zone_id: str, db: Session = Depends(db_dep)
) -> PowerForecastTrainOut:
    result = PowerForecastService(db).train(zone_id=zone_id)
    return PowerForecastTrainOut.model_validate(result)


@router.get(
    "/predictions/power/forecast/zones/{zone_id}",
    response_model=PowerLoadForecastOut,
)
def zone_power_load_forecast(
    zone_id: str, db: Session = Depends(db_dep)
) -> PowerLoadForecastOut:
    payload = PowerForecastService(db).load_forecast(zone_id=zone_id)
    return PowerLoadForecastOut.model_validate(payload)
