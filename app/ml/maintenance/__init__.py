"""
Predictive maintenance ML submodule (Sprint 6A + 6C).

Pipeline::

    Telemetry + Asset metadata (PostgreSQL)
         ↓
    loader.load_maintenance_telemetry
         ↓
    MaintenanceDatasetBuilder  (clean / sort / resample / pivot)
         ↓
    MaintenanceFeatureBuilder  (rolling stats, slopes, pump + asset attrs)
         ↓
    Deterministic operational labels
         ↓
    XGBoost model → artifacts/pump_xgboost.pkl
"""

from app.ml.maintenance.dataset import MaintenanceDatasetBuilder
from app.ml.maintenance.feature_builder import MaintenanceFeatureBuilder
from app.ml.maintenance.labeling import add_maintenance_labels
from app.ml.maintenance.loader import load_maintenance_telemetry
from app.ml.maintenance.xgboost_model import load_artifact, predict_xgboost, train_xgboost

__all__ = [
    "MaintenanceDatasetBuilder",
    "MaintenanceFeatureBuilder",
    "add_maintenance_labels",
    "load_maintenance_telemetry",
    "load_artifact",
    "predict_xgboost",
    "train_xgboost",
]
