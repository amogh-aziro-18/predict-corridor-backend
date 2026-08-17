"""
Demand / ops forecasting ML submodule (Sprint 6A + 6B).

Pipeline::

    Telemetry (PostgreSQL)
         ↓
    loader.load_demand_telemetry / load_forecast_telemetry
         ↓
    ForecastDatasetBuilder  (clean / sort / resample / demand series)
         ↓
    ForecastFeatureBuilder  (time, hour, day, flow_rate, …)
         ↓
    prophet_model           (train / predict — Sprint 6B)
         ↓
    artifacts/prophet_water.pkl
"""

from app.ml.forecasting.dataset import ForecastDatasetBuilder
from app.ml.forecasting.feature_builder import ForecastFeatureBuilder
from app.ml.forecasting.loader import load_demand_telemetry, load_forecast_telemetry
from app.ml.forecasting.prophet_model import load_artifact, predict_prophet, train_prophet

__all__ = [
    "ForecastDatasetBuilder",
    "ForecastFeatureBuilder",
    "load_demand_telemetry",
    "load_forecast_telemetry",
    "load_artifact",
    "predict_prophet",
    "train_prophet",
]
