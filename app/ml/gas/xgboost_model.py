"""
XGBoost Gas predictive-maintenance model.

Owns fitting, serialization, cached loading, and inference for
``gas_xgboost.pkl``. No FastAPI / repository dependencies.
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

from app.ml.artifacts import GAS_XGBOOST_PATH
from app.ml.gas.constants import MODEL_VERSION_GAS_XGBOOST
from app.ml.gas.features import MODEL_CATEGORICAL_FEATURES, MODEL_NUMERIC_FEATURES
from app.ml.gas.labeling import LABEL_COLUMN, label_distribution, label_strategy_name


@dataclass
class GasXGBoostArtifact:
    booster: Any
    model_version: str
    trained_at: datetime
    training_window: dict[str, Any]
    feature_names: list[str]
    numeric_medians: dict[str, float]
    training_metadata: dict[str, Any]


def train_gas_xgboost(
    labelled_features: pd.DataFrame,
    *,
    artifact_path: Path | None = None,
) -> GasXGBoostArtifact:
    """
    Train and persist a binary Gas XGBoost model.

    Temporal holdout (last 25% by time) for validation, then refit on full frame.
    Simulator-derived supervision — validation metrics are optimistic / not
    production-grade.
    """
    import xgboost as xgb

    if labelled_features is None or labelled_features.empty:
        raise ValueError("No Gas features available for XGBoost training.")
    if LABEL_COLUMN not in labelled_features.columns:
        raise ValueError(f"Training frame is missing target column '{LABEL_COLUMN}'.")

    frame = labelled_features.sort_values("time", kind="mergesort").reset_index(drop=True)
    labels = pd.to_numeric(frame[LABEL_COLUMN], errors="coerce").fillna(0).astype(int)
    distribution = label_distribution(frame)
    if labels.nunique() < 2:
        raise ValueError(
            "Gas XGBoost training requires both normal and maintenance-risk "
            f"examples. Current label distribution: {distribution}."
        )

    split_idx = max(1, int(len(frame) * 0.75))
    train_frame = frame.iloc[:split_idx].copy()
    val_frame = frame.iloc[split_idx:].copy()
    if train_frame[LABEL_COLUMN].nunique() < 2 or val_frame.empty:
        train_frame = frame.copy()
        val_frame = frame.iloc[split_idx:].copy() if len(frame) > split_idx else frame.tail(1).copy()

    train_labels = pd.to_numeric(train_frame[LABEL_COLUMN], errors="coerce").fillna(0).astype(int)
    if train_labels.nunique() < 2:
        raise ValueError(
            "Temporal train split lacks both classes. Generate more mixed Gas "
            f"telemetry before training. Distribution: {label_distribution(train_frame)}."
        )

    train_matrix, medians = _training_matrix(train_frame)
    dtrain = xgb.DMatrix(
        train_matrix,
        label=train_labels.to_numpy(),
        feature_names=list(train_matrix.columns),
        missing=np.nan,
    )

    positives = int(train_labels.sum())
    negatives = int(len(train_labels) - positives)
    scale_pos_weight = min(4.0, max(0.25, negatives / max(positives, 1)))
    params = {
        "objective": "binary:logistic",
        "eval_metric": "logloss",
        "max_depth": 3,
        "eta": 0.05,
        "subsample": 0.85,
        "colsample_bytree": 0.80,
        "min_child_weight": 3,
        "lambda": 1.5,
        "alpha": 0.1,
        "max_delta_step": 1,
        "scale_pos_weight": scale_pos_weight,
        "seed": 42,
        "nthread": 2,
    }
    holdout_booster = xgb.train(params, dtrain, num_boost_round=80)

    validation_metrics: dict[str, Any] = {}
    if len(val_frame) >= 4 and val_frame[LABEL_COLUMN].nunique() >= 1:
        val_matrix = _inference_matrix(
            val_frame,
            feature_names=list(train_matrix.columns),
            medians=medians,
        )
        dval = xgb.DMatrix(
            val_matrix,
            feature_names=list(train_matrix.columns),
            missing=np.nan,
        )
        val_prob = holdout_booster.predict(dval)
        val_true = (
            pd.to_numeric(val_frame[LABEL_COLUMN], errors="coerce").fillna(0).astype(int).to_numpy()
        )
        validation_metrics = _classification_metrics(val_true, val_prob)

    full_matrix, full_medians = _training_matrix(frame)
    dfull = xgb.DMatrix(
        full_matrix,
        label=labels.to_numpy(),
        feature_names=list(full_matrix.columns),
        missing=np.nan,
    )
    full_pos = int(labels.sum())
    full_neg = int(len(labels) - full_pos)
    params["scale_pos_weight"] = min(4.0, max(0.25, full_neg / max(full_pos, 1)))
    booster = xgb.train(params, dfull, num_boost_round=80)

    gain_importance = booster.get_score(importance_type="gain")
    ranked_importance = [
        {"feature": name, "gain": round(float(gain), 6)}
        for name, gain in sorted(
            gain_importance.items(),
            key=lambda item: item[1],
            reverse=True,
        )
    ]

    times = pd.to_datetime(frame.get("time"), utc=True, errors="coerce").dropna()
    trained_at = datetime.now(timezone.utc)
    artifact = GasXGBoostArtifact(
        booster=booster,
        model_version=MODEL_VERSION_GAS_XGBOOST,
        trained_at=trained_at,
        training_window={
            "start": times.min().to_pydatetime() if len(times) else None,
            "end": times.max().to_pydatetime() if len(times) else None,
            "rows": int(len(full_matrix)),
            "validation_rows": int(validation_metrics.get("rows", 0)),
        },
        feature_names=list(full_matrix.columns),
        numeric_medians=full_medians,
        training_metadata={
            "label_strategy": label_strategy_name(),
            "label_supervision": (
                "Simulator-derived operational-risk score from Gas telemetry bands "
                "(POC). Validation metrics are optimistic and not production-grade."
            ),
            "label_distribution": distribution,
            "asset_count": int(frame["asset_id"].nunique()) if "asset_id" in frame.columns else 0,
            "scale_pos_weight": float(params["scale_pos_weight"]),
            "parameters": {
                "max_depth": params["max_depth"],
                "learning_rate": params["eta"],
                "rounds": 80,
                "subsample": params["subsample"],
                "colsample_bytree": params["colsample_bytree"],
                "min_child_weight": params["min_child_weight"],
            },
            "numeric_features": list(MODEL_NUMERIC_FEATURES),
            "categorical_features": list(MODEL_CATEGORICAL_FEATURES),
            "validation_metrics": validation_metrics,
            "feature_importance_gain": ranked_importance,
            "assessment_kind": "predictive_maintenance_model",
            "utility_kind": "gas",
            "asset_types": ["gas_compressor", "gas_regulator_station", "gas_feeder"],
            "model_scope": "shared_gas_corridor_model",
        },
    )
    save_artifact(artifact, path=artifact_path)
    return artifact


def predict_gas_xgboost(
    artifact: GasXGBoostArtifact,
    features: pd.DataFrame,
) -> tuple[np.ndarray, np.ndarray]:
    """Return maintenance-risk probabilities and local feature contributions."""
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


def save_artifact(
    artifact: GasXGBoostArtifact,
    *,
    path: Path | None = None,
) -> Path:
    path = path or GAS_XGBOOST_PATH
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


def load_artifact(path: Path | None = None) -> GasXGBoostArtifact | None:
    path = path or GAS_XGBOOST_PATH
    if not path.exists():
        return None
    return _load_artifact_cached(str(path.resolve()), path.stat().st_mtime_ns)


@lru_cache(maxsize=8)
def _load_artifact_cached(path_text: str, _mtime_ns: int) -> GasXGBoostArtifact:
    with Path(path_text).open("rb") as fh:
        payload = pickle.load(fh)
    return GasXGBoostArtifact(
        booster=payload["booster"],
        model_version=str(payload.get("model_version", MODEL_VERSION_GAS_XGBOOST)),
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
    for column in MODEL_NUMERIC_FEATURES:
        values = pd.to_numeric(matrix[column], errors="coerce")
        median = float(values.median()) if values.notna().any() else 0.0
        medians[column] = median
        matrix[column] = values.fillna(median)
    matrix = pd.get_dummies(
        matrix,
        columns=list(MODEL_CATEGORICAL_FEATURES),
        prefix=list(MODEL_CATEGORICAL_FEATURES),
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
    for column in MODEL_NUMERIC_FEATURES:
        matrix[column] = pd.to_numeric(matrix[column], errors="coerce").fillna(
            medians.get(column, 0.0)
        )
    matrix = pd.get_dummies(
        matrix,
        columns=list(MODEL_CATEGORICAL_FEATURES),
        prefix=list(MODEL_CATEGORICAL_FEATURES),
        dtype=float,
    )
    return matrix.reindex(columns=feature_names, fill_value=0.0).astype(float)


def _base_matrix(frame: pd.DataFrame) -> pd.DataFrame:
    matrix = pd.DataFrame(index=frame.index)
    for column in MODEL_NUMERIC_FEATURES:
        matrix[column] = frame[column] if column in frame.columns else np.nan
    for column in MODEL_CATEGORICAL_FEATURES:
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


def _classification_metrics(y_true: np.ndarray, y_prob: np.ndarray) -> dict[str, Any]:
    y_true = np.asarray(y_true).astype(int)
    y_prob = np.asarray(y_prob).astype(float)
    y_pred = (y_prob >= 0.5).astype(int)
    tp = int(((y_pred == 1) & (y_true == 1)).sum())
    fp = int(((y_pred == 1) & (y_true == 0)).sum())
    fn = int(((y_pred == 0) & (y_true == 1)).sum())
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (
        2.0 * precision * recall / (precision + recall)
        if (precision + recall)
        else 0.0
    )
    metrics: dict[str, Any] = {
        "rows": int(len(y_true)),
        "precision": float(precision),
        "recall": float(recall),
        "f1": float(f1),
        "positive_rate": float(y_true.mean()) if len(y_true) else 0.0,
        "note": (
            "Simulator-derived supervision; metrics are optimistic and not "
            "production-grade."
        ),
    }
    if len(np.unique(y_true)) >= 2:
        metrics["roc_auc"] = float(_roc_auc(y_true, y_prob))
    else:
        metrics["roc_auc"] = None
    return metrics


def _roc_auc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    order = np.argsort(y_score)
    y_true = y_true[order]
    y_score = y_score[order]
    n_pos = float((y_true == 1).sum())
    n_neg = float((y_true == 0).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    ranks = np.empty(len(y_score), dtype=float)
    i = 0
    while i < len(y_score):
        j = i
        while j < len(y_score) and y_score[j] == y_score[i]:
            j += 1
        avg_rank = 0.5 * (i + j - 1) + 1.0
        ranks[i:j] = avg_rank
        i = j
    sum_pos_ranks = float(ranks[y_true == 1].sum())
    return (sum_pos_ranks - n_pos * (n_pos + 1.0) / 2.0) / (n_pos * n_neg)


__all__ = [
    "GasXGBoostArtifact",
    "load_artifact",
    "predict_gas_xgboost",
    "save_artifact",
    "train_gas_xgboost",
]
