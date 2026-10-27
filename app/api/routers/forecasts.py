"""Demand forecast API — Prophet over real PostgreSQL telemetry."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.forecast import (
    DemandForecastOut,
    ForecastModelStatusOut,
    ForecastTrainOut,
)
from app.services.forecast_service import ForecastService

router = APIRouter()


@router.get("/forecasts/demand/status", response_model=ForecastModelStatusOut)
def demand_forecast_status(db: Session = Depends(db_dep)) -> ForecastModelStatusOut:
    """Prophet artifact readiness — does not run inference or retrain."""
    payload = ForecastService(db).status()
    return ForecastModelStatusOut.model_validate(payload)


@router.get("/forecasts/demand", response_model=DemandForecastOut)
def corridor_demand_forecast(db: Session = Depends(db_dep)) -> DemandForecastOut:
    """Corridor-level water demand forecast (24h + 48h)."""
    payload = ForecastService(db).demand_forecast()
    return DemandForecastOut.model_validate(payload)


@router.get("/forecasts/demand/zones/{zone_id}", response_model=DemandForecastOut)
def zone_demand_forecast(zone_id: str, db: Session = Depends(db_dep)) -> DemandForecastOut:
    """Zone-level water demand forecast (reuses ForecastService)."""
    payload = ForecastService(db).demand_forecast(zone_id=zone_id)
    return DemandForecastOut.model_validate(payload)


@router.get(
    "/forecasts/demand/zones/{zone_id}/status",
    response_model=ForecastModelStatusOut,
)
def zone_demand_forecast_status(
    zone_id: str, db: Session = Depends(db_dep)
) -> ForecastModelStatusOut:
    """Zone Prophet readiness with corridor artifact fallback metadata."""
    payload = ForecastService(db).status(zone_id=zone_id)
    return ForecastModelStatusOut.model_validate(payload)


@router.post("/forecasts/demand/train", response_model=ForecastTrainOut)
def train_demand_forecast(db: Session = Depends(db_dep)) -> ForecastTrainOut:
    """
    Explicit Prophet retrain from PostgreSQL telemetry.

    Not called automatically by GET forecast endpoints.
    """
    result = ForecastService(db).train()
    return ForecastTrainOut.model_validate(result)


@router.post(
    "/forecasts/demand/zones/{zone_id}/train",
    response_model=ForecastTrainOut,
)
def train_zone_demand_forecast(zone_id: str, db: Session = Depends(db_dep)) -> ForecastTrainOut:
    """Explicit zone-scoped Prophet retrain."""
    result = ForecastService(db).train(zone_id=zone_id)
    return ForecastTrainOut.model_validate(result)
