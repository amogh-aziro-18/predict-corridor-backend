"""Gas demand forecast API — Prophet over Gas PostgreSQL telemetry."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.gas_forecast import (
    GasDemandForecastOut,
    GasForecastModelStatusOut,
    GasForecastTrainOut,
)
from app.services.gas_forecast_service import GasForecastService

router = APIRouter()


@router.get(
    "/predictions/gas/forecast/status",
    response_model=GasForecastModelStatusOut,
)
def gas_forecast_status(db: Session = Depends(db_dep)) -> GasForecastModelStatusOut:
    payload = GasForecastService(db).status()
    return GasForecastModelStatusOut.model_validate(payload)


@router.post(
    "/predictions/gas/forecast/train",
    response_model=GasForecastTrainOut,
)
def train_gas_forecast(db: Session = Depends(db_dep)) -> GasForecastTrainOut:
    result = GasForecastService(db).train()
    return GasForecastTrainOut.model_validate(result)


@router.get(
    "/predictions/gas/forecast",
    response_model=GasDemandForecastOut,
)
def gas_demand_forecast(db: Session = Depends(db_dep)) -> GasDemandForecastOut:
    """Corridor Gas demand forecast (flow_rate_nm3_h sum, preferred layer)."""
    payload = GasForecastService(db).demand_forecast()
    return GasDemandForecastOut.model_validate(payload)


@router.get(
    "/predictions/gas/forecast/zones/{zone_id}/status",
    response_model=GasForecastModelStatusOut,
)
def zone_gas_forecast_status(
    zone_id: str, db: Session = Depends(db_dep)
) -> GasForecastModelStatusOut:
    payload = GasForecastService(db).status(zone_id=zone_id)
    return GasForecastModelStatusOut.model_validate(payload)


@router.post(
    "/predictions/gas/forecast/zones/{zone_id}/train",
    response_model=GasForecastTrainOut,
)
def train_zone_gas_forecast(
    zone_id: str, db: Session = Depends(db_dep)
) -> GasForecastTrainOut:
    result = GasForecastService(db).train(zone_id=zone_id)
    return GasForecastTrainOut.model_validate(result)


@router.get(
    "/predictions/gas/forecast/zones/{zone_id}",
    response_model=GasDemandForecastOut,
)
def zone_gas_demand_forecast(
    zone_id: str, db: Session = Depends(db_dep)
) -> GasDemandForecastOut:
    payload = GasForecastService(db).demand_forecast(zone_id=zone_id)
    return GasDemandForecastOut.model_validate(payload)
