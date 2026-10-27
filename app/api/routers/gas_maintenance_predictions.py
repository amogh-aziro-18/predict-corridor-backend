"""Gas predictive-maintenance API — Phase 15.3."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.api.schemas.gas_maintenance_prediction import (
    GasMaintenanceModelStatusOut,
    GasMaintenancePredictionOut,
    GasMaintenanceTrainOut,
)
from app.services.gas_predictive_maintenance_service import (
    GasPredictiveMaintenanceService,
)

router = APIRouter()


@router.post(
    "/predictions/gas/maintenance/train",
    response_model=GasMaintenanceTrainOut,
)
def train_gas_maintenance_model(db: Session = Depends(db_dep)) -> GasMaintenanceTrainOut:
    result = GasPredictiveMaintenanceService(db).train()
    return GasMaintenanceTrainOut.model_validate(result)


@router.get(
    "/predictions/gas/maintenance/status",
    response_model=GasMaintenanceModelStatusOut,
)
def gas_maintenance_model_status(
    db: Session = Depends(db_dep),
) -> GasMaintenanceModelStatusOut:
    result = GasPredictiveMaintenanceService(db).status()
    return GasMaintenanceModelStatusOut.model_validate(result)


@router.get(
    "/predictions/gas/maintenance",
    response_model=list[GasMaintenancePredictionOut],
)
def list_gas_maintenance_predictions(
    db: Session = Depends(db_dep),
) -> list[GasMaintenancePredictionOut]:
    predictions = GasPredictiveMaintenanceService(db).predict_all()
    return [GasMaintenancePredictionOut.model_validate(item) for item in predictions]


@router.get(
    "/predictions/gas/maintenance/{asset_id}",
    response_model=GasMaintenancePredictionOut,
)
def get_gas_maintenance_prediction(
    asset_id: str,
    db: Session = Depends(db_dep),
) -> GasMaintenancePredictionOut:
    result = GasPredictiveMaintenanceService(db).predict_asset(asset_id)
    return GasMaintenancePredictionOut.model_validate(result)
