"""
XGBoost predictive-maintenance model over PostgreSQL-derived feature frames.

This module owns model fitting, serialization, cached loading, numeric matrix
preparation, probabilities, and per-row XGBoost contribution values. It has no
FastAPI or repository dependencies.
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.ml.artifacts import PUMP_XGBOOST_PATH
from app.ml.common.constants import MODEL_VERSION_XGBOOST
from app.ml.maintenance.labeling import LABEL_COLUMN, label_distribution


NUMERIC_FEATURES: tuple[str, ...] = (
    "pressure",
    "flow_rate",
    "pump_speed",
    "pump_running",
    "pressure_rolling_mean",
    "pressure_rolling_std",
    "pressure_rolling_min",
    "pressure_rolling_max",
    "pressure_variance",
    "flow_rolling_mean",
    "flow_rolling_std",
    "flow_rolling_min",
    "flow_rolling_max",
    "flow_variance",
    "pump_speed_rolling_mean",
    "pump_speed_rolling_std",
    "pressure_slope",
    "flow_slope",
    "pressure_rate_of_change",
    "flow_rate_of_change",
    "pump_utilization",
    "operating_duration_minutes",
    "health",
    "scenario_intensity",
)

CATEGORICAL_FEATURES: tuple[str, ...] = ("status", "zone", "criticality")


@dataclass
class XGBoostArtifact:
    booster: Any
    model_version: str
    trained_at: datetime
    training_window: dict[str, Any]
    feature_names: list[str]
    numeric_medians: dict[str, float]
    training_metadata: dict[str, Any]


def train_xgboost(
    labelled_features: pd.DataFrame,
    *,
    artifact_path: Path | None = None,
) -> XGBoostArtifact:
    """Train and persist a binary XGBoost model from deterministic labels."""
    import xgboost as xgb

    if labelled_features is None or labelled_features.empty:
        raise ValueError("No maintenance features available for XGBoost training.")
    if LABEL_COLUMN not in labelled_features.columns:
        raise ValueError(f"Training frame is missing target column '{LABEL_COLUMN}'.")

    labels = pd.to_numeric(labelled_features[LABEL_COLUMN], errors="coerce").fillna(0).astype(int)
    if labels.nunique() < 2:
        distribution = label_distribution(labelled_features)
        raise ValueError(
            "XGBoost training requires both normal and maintenance-risk examples. "
            f"Current PostgreSQL-derived label distribution: {distribution}."
        )

    matrix, medians = _training_matrix(labelled_features)
    dtrain = xgb.DMatrix(
        matrix,
        label=labels.to_numpy(),
        feature_names=list(matrix.columns),
        missing=np.nan,
    )

    positives = int(labels.sum())
    negatives = int(len(labels) - positives)
    # Balance in both directions. The previous min=1 behavior did not
    # down-weight the majority positive class and encouraged saturation.
    scale_pos_weight = min(4.0, max(0.25, negatives / max(positives, 1)))
    params = {
        "objective": "binary:logistic",
        "eval_metric": "logloss",
        "max_depth": 2,
        "eta": 0.03,
        "subsample": 0.80,
        "colsample_bytree": 0.75,
        "min_child_weight": 4,
        "lambda": 2.0,
        "alpha": 0.2,
        "max_delta_step": 1,
        "scale_pos_weight": scale_pos_weight,
        "seed": 42,
        "nthread": 2,
    }
    booster = xgb.train(params, dtrain, num_boost_round=60)
    gain_importance = booster.get_score(importance_type="gain")
    ranked_importance = [
        {"feature": name, "gain": round(float(gain), 6)}
        for name, gain in sorted(
            gain_importance.items(),
            key=lambda item: item[1],
            reverse=True,
        )
    ]

    times = pd.to_datetime(labelled_features.get("time"), utc=True, errors="coerce").dropna()
    trained_at = datetime.now(timezone.utc)
    artifact = XGBoostArtifact(
        booster=booster,
        model_version=MODEL_VERSION_XGBOOST,
        trained_at=trained_at,
        training_window={
            "start": times.min().to_pydatetime() if len(times) else None,
            "end": times.max().to_pydatetime() if len(times) else None,
            "rows": int(len(matrix)),
        },
        feature_names=list(matrix.columns),
        numeric_medians=medians,
        training_metadata={
            "label_strategy": "deterministic_operational_risk_v2",
            "label_distribution": label_distribution(labelled_features),
            "asset_count": int(labelled_features["asset_id"].nunique()),
            "scale_pos_weight": float(scale_pos_weight),
            "parameters": {
                "max_depth": params["max_depth"],
                "learning_rate": params["eta"],
                "rounds": 60,
                "subsample": params["subsample"],
                "colsample_bytree": params["colsample_bytree"],
                "min_child_weight": params["min_child_weight"],
            },
            "feature_importance_gain": ranked_importance,
        },
    )
    save_artifact(artifact, path=artifact_path)
    return artifact


def predict_xgboost(
    artifact: XGBoostArtifact,
    features: pd.DataFrame,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Return risk probabilities and local feature contribution values.

    Contributions use XGBoost's native ``pred_contribs`` output. The final
    column is the bias term and is omitted from the returned matrix.
    """
    import xgboost as xgb

    matrix = _inference_matrix(
        features,
        feature_names=artifact.feature_names,
        medians=artifact.numeric_medians,
    )
    dmatrix = xgb.DMatrix(
        matrix,
        feature_names=artifact.feature_names,
        missing=np.nan,
    )
    probabilities = artifact.booster.predict(dmatrix)
    contributions = artifact.booster.predict(dmatrix, pred_contribs=True)
    return probabilities, contributions[:, :-1]


def save_artifact(artifact: XGBoostArtifact, *, path: Path | None = None) -> Path:
    path = path or PUMP_XGBOOST_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "booster": artifact.booster,
        "model_version": artifact.model_version,
        "trained_at": artifact.trained_at,
        "training_window": artifact.training_window,
        "feature_names": artifact.feature_names,
        "numeric_medians": artifact.numeric_medians,
        "training_metadata": artifact.training_metadata,
    }
    with path.open("wb") as fh:
        pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
    _load_artifact_cached.cache_clear()
    return path


def load_artifact(path: Path | None = None) -> XGBoostArtifact | None:
    """Load once per artifact path + mtime and reuse across service instances."""
    path = path or PUMP_XGBOOST_PATH
    if not path.exists():
        return None
    return _load_artifact_cached(str(path.resolve()), path.stat().st_mtime_ns)


@lru_cache(maxsize=8)
def _load_artifact_cached(path_text: str, _mtime_ns: int) -> XGBoostArtifact:
    with Path(path_text).open("rb") as fh:
        payload = pickle.load(fh)
    return XGBoostArtifact(
        booster=payload["booster"],
        model_version=str(payload.get("model_version", MODEL_VERSION_XGBOOST)),
        trained_at=_as_utc(payload.get("trained_at")),
        training_window=dict(payload.get("training_window") or {}),
        feature_names=list(payload.get("feature_names") or []),
        numeric_medians={
            str(key): float(value)
            for key, value in dict(payload.get("numeric_medians") or {}).items()
        },
        training_metadata=dict(payload.get("training_metadata") or {}),
    )


def _training_matrix(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float]]:
    matrix = _base_matrix(frame)
    medians: dict[str, float] = {}
    for column in NUMERIC_FEATURES:
        values = pd.to_numeric(matrix[column], errors="coerce")
        median = float(values.median()) if values.notna().any() else 0.0
        medians[column] = median
        matrix[column] = values.fillna(median)
    matrix = pd.get_dummies(
        matrix,
        columns=list(CATEGORICAL_FEATURES),
        prefix=list(CATEGORICAL_FEATURES),
        dtype=float,
    )
    return matrix.astype(float), medians


def _inference_matrix(
    frame: pd.DataFrame,
    *,
    feature_names: list[str],
    medians: dict[str, float],
) -> pd.DataFrame:
    matrix = _base_matrix(frame)
    for column in NUMERIC_FEATURES:
        matrix[column] = pd.to_numeric(matrix[column], errors="coerce").fillna(
            medians.get(column, 0.0)
        )
    matrix = pd.get_dummies(
        matrix,
        columns=list(CATEGORICAL_FEATURES),
        prefix=list(CATEGORICAL_FEATURES),
        dtype=float,
    )
    return matrix.reindex(columns=feature_names, fill_value=0.0).astype(float)


def _base_matrix(frame: pd.DataFrame) -> pd.DataFrame:
    matrix = pd.DataFrame(index=frame.index)
    for column in NUMERIC_FEATURES:
        matrix[column] = frame[column] if column in frame.columns else np.nan
    for column in CATEGORICAL_FEATURES:
        matrix[column] = (
            frame[column].fillna("unknown").astype(str)
            if column in frame.columns
            else "unknown"
        )
    return matrix


def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if value is None:
        return datetime.now(timezone.utc)
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.to_pydatetime().replace(tzinfo=timezone.utc)
    return timestamp.tz_convert("UTC").to_pydatetime()


__all__ = [
    "CATEGORICAL_FEATURES",
    "NUMERIC_FEATURES",
    "XGBoostArtifact",
    "load_artifact",
    "predict_xgboost",
    "save_artifact",
    "train_xgboost",
]
