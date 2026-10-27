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
    # Include Power transformer PdM in the multi-model snapshot without
    # coupling Water training/inference to Power.
    from app.services.power_predictive_maintenance_service import (
        PowerPredictiveMaintenanceService,
    )

    power_status = PowerPredictiveMaintenanceService(db).status()
    models = list(result.get("models") or [])
    models.append(
        {
            "model": power_status["model"],
            "model_version": power_status.get("model_version"),
            "artifact_exists": power_status["artifact_exists"],
            "artifact_name": power_status["artifact_path"],
            "inference": power_status["inference"],
        }
    )
    from app.services.power_forecast_service import PowerForecastService

    forecast_status = PowerForecastService(db).status()
    models.append(
        {
            "model": forecast_status["model"],
            "model_version": forecast_status.get("model_version"),
            "artifact_exists": forecast_status["artifact_exists"],
            "artifact_name": forecast_status["artifact_path"],
            "inference": forecast_status["inference"],
        }
    )
    from app.services.gas_forecast_service import GasForecastService

    gas_forecast_status = GasForecastService(db).status()
    models.append(
        {
            "model": gas_forecast_status["model"],
            "model_version": gas_forecast_status.get("model_version"),
            "artifact_exists": gas_forecast_status["artifact_exists"],
            "artifact_name": gas_forecast_status["artifact_path"],
            "inference": gas_forecast_status["inference"],
        }
    )
    result["models"] = models
    return MaintenanceModelStatusOut.model_validate(result)
