"""Gas predictive-maintenance orchestration over PostgreSQL telemetry."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationAppError
from app.domain.repositories import AssetRepository
from app.ml.artifacts import GAS_XGBOOST_PATH
from app.ml.gas.constants import (
    DEFAULT_RESAMPLE_ENABLED,
    GAS_ASSET_TYPES,
    MIN_NEGATIVE_ROWS,
    MIN_POSITIVE_ROWS,
    MIN_TRAINING_ROWS,
    MODEL_VERSION_GAS_XGBOOST,
)
from app.ml.gas.dataset import GasDatasetBuilder
from app.ml.gas.features import MODEL_NUMERIC_FEATURES, GasFeatureBuilder
from app.ml.gas.labeling import (
    LABEL_COLUMN,
    add_gas_labels,
    label_distribution,
    label_strategy_name,
)
from app.ml.gas.xgboost_model import (
    GasXGBoostArtifact,
    load_artifact,
    predict_gas_xgboost,
    train_gas_xgboost,
)


class GasPredictiveMaintenanceService:
    """
    Prepare Gas features, train XGBoost, and run inference.

    Distinct from rule-based Gas condition assessment (Phase 15.1).
    Prediction never retrains. Artifact is cached after first successful load.

    Shared corridor model for CMP / REG / GFDR (not zone-specific).
    """

    ASSESSMENT_KIND = "predictive_maintenance_model"
    MODEL_NAME = "gas_xgboost"

    def __init__(self, db: Session) -> None:
        self._db = db
        self._assets = AssetRepository(db)
        self._datasets = GasDatasetBuilder(db)
        self._features = GasFeatureBuilder()
        self._artifact: GasXGBoostArtifact | None = None

    def load_dataset(
        self,
        *,
        asset_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 50_000,
        resample: bool = DEFAULT_RESAMPLE_ENABLED,
    ) -> pd.DataFrame:
        return self._datasets.build(
            asset_id=asset_id,
            start=start,
            end=end,
            limit=limit,
            resample=resample,
        )

    def prepare_features(
        self,
        *,
        asset_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 50_000,
        resample: bool = DEFAULT_RESAMPLE_ENABLED,
    ) -> pd.DataFrame:
        dataset = self.load_dataset(
            asset_id=asset_id,
            start=start,
            end=end,
            limit=limit,
            resample=resample,
        )
        return self._features.build(dataset)

    def preparation_summary(
        self,
        *,
        asset_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 50_000,
    ) -> dict[str, Any]:
        features = self.prepare_features(
            asset_id=asset_id,
            start=start,
            end=end,
            limit=limit,
        )
        asset_count = int(features["asset_id"].nunique()) if len(features) else 0
        return {
            "status": "prepared",
            "model": self.MODEL_NAME,
            "utility_kind": "gas",
            "assessment_kind": self.ASSESSMENT_KIND,
            "artifact_path": str(GAS_XGBOOST_PATH),
            "artifact_exists": GAS_XGBOOST_PATH.exists(),
            "rows": int(len(features)),
            "assets": asset_count,
            "feature_columns": list(MODEL_NUMERIC_FEATURES),
            "inference": "available" if GAS_XGBOOST_PATH.exists() else "train_required",
        }

    def train(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100_000,
    ) -> dict[str, Any]:
        """
        Explicitly train Gas XGBoost from Gas telemetry + deterministic labels.

        Labels come from ``app.ml.gas.labeling`` (operational metric evidence),
        not simulator scenario / zone names. POC supervision only.
        """
        features = self.prepare_features(
            start=start,
            end=end,
            limit=limit,
            resample=DEFAULT_RESAMPLE_ENABLED,
        )
        if features.empty:
            raise ValidationAppError(
                "No Gas telemetry available in PostgreSQL for maintenance training. "
                "Run the Gas simulator first."
            )

        labelled = add_gas_labels(features)
        distribution = label_distribution(labelled)
        rows = int(len(labelled))
        positives = int(distribution.get("maintenance_risk", 0))
        negatives = int(distribution.get("normal", 0))
        if rows < MIN_TRAINING_ROWS:
            raise ValidationAppError(
                f"Insufficient Gas training rows ({rows} < {MIN_TRAINING_ROWS}). "
                "Run the Gas simulator longer (mixed mode), then retry training."
            )
        if positives < MIN_POSITIVE_ROWS or negatives < MIN_NEGATIVE_ROWS:
            raise ValidationAppError(
                "Insufficient class balance for Gas XGBoost training. "
                f"Need at least {MIN_POSITIVE_ROWS} positive and {MIN_NEGATIVE_ROWS} "
                f"negative samples; got distribution={distribution}."
            )

        try:
            artifact = train_gas_xgboost(labelled, artifact_path=GAS_XGBOOST_PATH)
        except ValueError as exc:
            raise ValidationAppError(str(exc)) from exc

        self._artifact = artifact
        return {
            "status": "trained",
            "model": self.MODEL_NAME,
            "model_version": artifact.model_version,
            "utility_kind": "gas",
            "assessment_kind": self.ASSESSMENT_KIND,
            "artifact_path": GAS_XGBOOST_PATH.name,
            "trained_at": artifact.trained_at,
            "training_window": artifact.training_window,
            "rows": rows,
            "assets": int(labelled["asset_id"].nunique()),
            "label_distribution": distribution,
            "label_strategy": artifact.training_metadata.get(
                "label_strategy", label_strategy_name()
            ),
            "validation_metrics": artifact.training_metadata.get("validation_metrics", {}),
            "feature_names": list(artifact.feature_names),
        }

    def predict_all(self) -> list[dict[str, Any]]:
        return self._predict(asset_id=None)

    def predict_asset(self, asset_id: str) -> dict[str, Any]:
        asset = self._assets.get(asset_id)
        if asset is None:
            raise NotFoundError(f"Asset not found: {asset_id}")
        if asset.utility_kind != "gas" or asset.type not in GAS_ASSET_TYPES:
            raise ValidationAppError(
                f"Gas PdM supports utility=gas types={list(GAS_ASSET_TYPES)}; "
                f"{asset_id} is utility={asset.utility_kind} type={asset.type}."
            )
        predictions = self._predict(asset_id=asset_id)
        if not predictions:
            raise NotFoundError(f"No Gas maintenance telemetry found for asset: {asset_id}")
        return predictions[0]

    def status(self) -> dict[str, Any]:
        artifact = load_artifact(GAS_XGBOOST_PATH)
        exists = artifact is not None
        return {
            "model": self.MODEL_NAME,
            "model_version": artifact.model_version if artifact else None,
            "utility_kind": "gas",
            "assessment_kind": self.ASSESSMENT_KIND,
            "artifact_exists": exists,
            "artifact_path": GAS_XGBOOST_PATH.name,
            "last_training_time": artifact.trained_at if artifact else None,
            "training_window": artifact.training_window if artifact else {},
            "training_metadata": artifact.training_metadata if artifact else {},
            "inference": "available" if exists else "train_required",
            "expected_model_version": MODEL_VERSION_GAS_XGBOOST,
        }

    def _predict(self, *, asset_id: str | None) -> list[dict[str, Any]]:
        artifact = self._ensure_artifact()
        features = self.prepare_features(
            asset_id=asset_id,
            limit=100_000 if asset_id is None else 50_000,
            resample=DEFAULT_RESAMPLE_ENABLED,
        )
        if features.empty:
            return []

        latest = (
            features.sort_values(["asset_id", "time"], kind="mergesort")
            .groupby("asset_id", as_index=False)
            .tail(1)
            .reset_index(drop=True)
        )
        probabilities, contributions = predict_gas_xgboost(artifact, latest)
        generated_at = datetime.now(timezone.utc)

        output: list[dict[str, Any]] = []
        for row_index, (_, row) in enumerate(latest.iterrows()):
            probability = float(probabilities[row_index])
            evidence = {
                "pressure_bar": _maybe_float(row.get("pressure_bar")),
                "leak_indicator": _maybe_float(row.get("leak_indicator")),
                "compressor_load_pct": _maybe_float(row.get("compressor_load_pct")),
                "vibration_mm_s": _maybe_float(row.get("vibration_mm_s")),
                "temperature_c": _maybe_float(row.get("temperature_c")),
                "gas_quality_pct": _maybe_float(row.get("gas_quality_pct")),
                "asset_type": str(row.get("asset_type") or ""),
            }
            output.append(
                {
                    "asset_id": str(row["asset_id"]),
                    "asset_name": str(row.get("asset_name") or row["asset_id"]),
                    "utility_kind": "gas",
                    "affected_zone": str(row.get("zone") or "") or None,
                    "model_name": self.MODEL_NAME,
                    "model_version": artifact.model_version,
                    "assessment_kind": self.ASSESSMENT_KIND,
                    "prediction": "maintenance_risk"
                    if probability >= 0.5
                    else "normal",
                    "maintenance_risk": round(probability, 4),
                    "risk_probability": round(probability, 4),
                    "risk_level": self._risk_level(probability),
                    # Distance from 0.5 — not fabricated accuracy %.
                    "model_certainty": round(0.5 + abs(probability - 0.5), 4),
                    "top_contributing_features": self._top_contributors(
                        artifact.feature_names,
                        contributions[row_index],
                    ),
                    "feature_evidence": evidence,
                    "generated_at": generated_at,
                    "model_available": True,
                }
            )
        return sorted(output, key=lambda item: item["maintenance_risk"], reverse=True)

    def _ensure_artifact(self) -> GasXGBoostArtifact:
        if self._artifact is not None:
            return self._artifact
        artifact = load_artifact(GAS_XGBOOST_PATH)
        if artifact is None:
            raise ValidationAppError(
                "Gas XGBoost model not trained. Call POST "
                "/api/predictions/gas/maintenance/train before requesting predictions."
            )
        self._artifact = artifact
        return artifact

    @staticmethod
    def _risk_level(probability: float) -> str:
        if probability >= 0.85:
            return "critical"
        if probability >= 0.65:
            return "high"
        if probability >= 0.40:
            return "medium"
        return "low"

    @staticmethod
    def _top_contributors(
        feature_names: list[str],
        contributions: Any,
        *,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        ranked = sorted(
            zip(feature_names, contributions, strict=True),
            key=lambda item: abs(float(item[1])),
            reverse=True,
        )[:limit]
        return [
            {
                "feature": name,
                "contribution": round(float(value), 5),
                "direction": "increases_risk" if float(value) >= 0 else "reduces_risk",
            }
            for name, value in ranked
        ]


def _maybe_float(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
