"""Power transformer predictive-maintenance API — Phase 14.3C1."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.power_maintenance_prediction import (
    PowerMaintenanceModelStatusOut,
    PowerMaintenancePredictionOut,
    PowerMaintenanceTrainOut,
)
from app.services.power_predictive_maintenance_service import (
    PowerPredictiveMaintenanceService,
)

router = APIRouter()


@router.post(
    "/predictions/power/maintenance/train",
    response_model=PowerMaintenanceTrainOut,
)
def train_power_maintenance_model(db: Session = Depends(db_dep)) -> PowerMaintenanceTrainOut:
    result = PowerPredictiveMaintenanceService(db).train()
    return PowerMaintenanceTrainOut.model_validate(result)


@router.get(
    "/predictions/power/maintenance/status",
    response_model=PowerMaintenanceModelStatusOut,
)
def power_maintenance_model_status(
    db: Session = Depends(db_dep),
) -> PowerMaintenanceModelStatusOut:
    result = PowerPredictiveMaintenanceService(db).status()
    return PowerMaintenanceModelStatusOut.model_validate(result)


@router.get(
    "/predictions/power/maintenance",
    response_model=list[PowerMaintenancePredictionOut],
)
def list_power_maintenance_predictions(
    db: Session = Depends(db_dep),
) -> list[PowerMaintenancePredictionOut]:
    predictions = PowerPredictiveMaintenanceService(db).predict_all()
    return [PowerMaintenancePredictionOut.model_validate(item) for item in predictions]


@router.get(
    "/predictions/power/maintenance/{asset_id}",
    response_model=PowerMaintenancePredictionOut,
)
def get_power_maintenance_prediction(
    asset_id: str,
    db: Session = Depends(db_dep),
) -> PowerMaintenancePredictionOut:
    result = PowerPredictiveMaintenanceService(db).predict_asset(asset_id)
    return PowerMaintenancePredictionOut.model_validate(result)
