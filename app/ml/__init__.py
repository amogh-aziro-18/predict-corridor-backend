"""
Machine-learning package for Predict Corridor (Sprint 6).

Architecture pipeline::

    Simulator
         ↓
    Telemetry  (PostgreSQL ``telemetry`` table — source of truth)
         ↓
    History API  (GET /api/telemetry/history)
         ↓
    Dataset Builder  (app/ml/*/dataset.py — clean, sort, resample)
         ↓
    Feature Builder  (app/ml/*/feature_builder.py — engineered columns)
         ↓
    ML Models (Prophet demand + XGBoost maintenance; artifacts under app/ml/artifacts/)
         ↓
    Services (forecast / predictive maintenance)
         ↓
    Operational Intelligence + Recommendation Service
         ↓
    Frontend APIs

Services (``ForecastService``, ``PredictiveMaintenanceService``) orchestrate
training/inference. Routers stay thin; model code stays in ``app/ml``.
"""

from app.ml.artifacts import ARTIFACT_DIR, PROPHET_WATER_PATH, PUMP_XGBOOST_PATH

__all__ = [
    "ARTIFACT_DIR",
    "PROPHET_WATER_PATH",
    "PUMP_XGBOOST_PATH",
]
