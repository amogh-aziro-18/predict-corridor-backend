"""Gas predictive-maintenance package — Phase 15.3."""

from app.ml.gas.constants import MODEL_VERSION_GAS_XGBOOST
from app.ml.gas.features import MODEL_CATEGORICAL_FEATURES, MODEL_NUMERIC_FEATURES
from app.ml.gas.xgboost_model import load_artifact, predict_gas_xgboost, train_gas_xgboost

__all__ = [
    "MODEL_CATEGORICAL_FEATURES",
    "MODEL_NUMERIC_FEATURES",
    "MODEL_VERSION_GAS_XGBOOST",
    "load_artifact",
    "predict_gas_xgboost",
    "train_gas_xgboost",
]
