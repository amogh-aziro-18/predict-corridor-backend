"""Predictive-maintenance API — thin service adapters."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.maintenance_prediction import (
    MaintenanceModelStatusOut,
    MaintenancePredictionOut,
    MaintenanceTrainOut,
)
from app.services.predictive_maintenance_service import PredictiveMaintenanceService

router = APIRouter()


@router.post(
    "/predictions/maintenance/train",
    response_model=MaintenanceTrainOut,
)
def train_maintenance_model(db: Session = Depends(db_dep)) -> MaintenanceTrainOut:
    result = PredictiveMaintenanceService(db).train()
    return MaintenanceTrainOut.model_validate(result)


@router.get(
    "/predictions/maintenance",
    response_model=list[MaintenancePredictionOut],
)
def list_maintenance_predictions(
    db: Session = Depends(db_dep),
) -> list[MaintenancePredictionOut]:
    predictions = PredictiveMaintenanceService(db).predict_all()
    return [MaintenancePredictionOut.model_validate(item) for item in predictions]


@router.get(
    "/predictions/maintenance/{asset_id}",
    response_model=MaintenancePredictionOut,
)
def get_maintenance_prediction(
    asset_id: str,
    db: Session = Depends(db_dep),
) -> MaintenancePredictionOut:
    result = PredictiveMaintenanceService(db).predict_asset(asset_id)
    return MaintenancePredictionOut.model_validate(result)


@router.get("/ml/status", response_model=MaintenanceModelStatusOut)
def maintenance_model_status(
    db: Session = Depends(db_dep),
) -> MaintenanceModelStatusOut:
    result = PredictiveMaintenanceService(db).status()
    return MaintenanceModelStatusOut.model_validate(result)
