"""Predictive-maintenance orchestration over PostgreSQL telemetry."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationAppError
from app.domain.repositories import AssetRepository
from app.ml.artifacts import PUMP_XGBOOST_PATH
from app.ml.maintenance.dataset import MaintenanceDatasetBuilder
from app.ml.maintenance.feature_builder import MaintenanceFeatureBuilder
from app.ml.maintenance.labeling import add_maintenance_labels, label_distribution
from app.ml.maintenance.xgboost_model import (
    XGBoostArtifact,
    load_artifact,
    predict_xgboost,
    train_xgboost,
)


class PredictiveMaintenanceService:
    """
    Prepare features, explicitly train XGBoost, and perform inference.

    Prediction never retrains. The persisted model is cached by the ML layer
    and reused across service instances.
    """

    def __init__(self, db: Session) -> None:
        self._db = db
        self._assets = AssetRepository(db)
        self._datasets = MaintenanceDatasetBuilder(db)
        self._features = MaintenanceFeatureBuilder()
        self._artifact: XGBoostArtifact | None = None

    def load_dataset(
        self,
        *,
        asset_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 50_000,
        resample: bool = True,
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
        resample: bool = True,
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
        """Diagnostics for the PostgreSQL-backed maintenance feature pipeline."""
        features = self.prepare_features(
            asset_id=asset_id,
            start=start,
            end=end,
            limit=limit,
        )
        asset_count = int(features["asset_id"].nunique()) if len(features) else 0
        return {
            "status": "prepared",
            "model": "pump_xgboost",
            "artifact_path": str(PUMP_XGBOOST_PATH),
            "artifact_exists": PUMP_XGBOOST_PATH.exists(),
            "rows": int(len(features)),
            "assets": asset_count,
            "columns": list(features.columns),
            "inference": "available" if PUMP_XGBOOST_PATH.exists() else "train_required",
        }

    def train(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100_000,
    ) -> dict[str, Any]:
        """
        Explicitly train XGBoost from real telemetry and deterministic labels.

        Labels are derived by ``app.ml.maintenance.labeling`` from hydraulic
        threshold violations, pump availability, health, and asset status.
        """
        features = self.prepare_features(
            start=start,
            end=end,
            limit=limit,
            resample=True,
        )
        if features.empty:
            raise ValidationAppError(
                "No pump telemetry available in PostgreSQL for maintenance training."
            )

        labelled = add_maintenance_labels(features)
        distribution = label_distribution(labelled)
        try:
            artifact = train_xgboost(labelled, artifact_path=PUMP_XGBOOST_PATH)
        except ValueError as exc:
            raise ValidationAppError(str(exc)) from exc

        self._artifact = artifact
        return {
            "status": "trained",
            "model_version": artifact.model_version,
            "artifact_path": str(PUMP_XGBOOST_PATH),
            "trained_at": artifact.trained_at,
            "training_window": artifact.training_window,
            "rows": int(len(labelled)),
            "assets": int(labelled["asset_id"].nunique()),
            "label_distribution": distribution,
            "label_strategy": artifact.training_metadata.get("label_strategy"),
        }

    def predict_all(self) -> list[dict[str, Any]]:
        """Return latest maintenance risk for every pump with telemetry."""
        return self._predict(asset_id=None)

    def predict_asset(self, asset_id: str) -> dict[str, Any]:
        """Return latest maintenance risk for one pump asset."""
        asset = self._assets.get(asset_id)
        if asset is None:
            raise NotFoundError(f"Asset not found: {asset_id}")
        if asset.type != "pump_station":
            raise ValidationAppError(
                f"Predictive maintenance model supports pump_station assets; "
                f"{asset_id} is '{asset.type}'."
            )
        predictions = self._predict(asset_id=asset_id)
        if not predictions:
            raise NotFoundError(f"No maintenance telemetry found for asset: {asset_id}")
        return predictions[0]

    def status(self) -> dict[str, Any]:
        """Model artifact and training metadata without running inference."""
        artifact = load_artifact(PUMP_XGBOOST_PATH)
        return {
            "model": "pump_xgboost",
            "model_version": artifact.model_version if artifact else None,
            "artifact_exists": artifact is not None,
            "artifact_path": str(PUMP_XGBOOST_PATH),
            "last_training_time": artifact.trained_at if artifact else None,
            "training_window": artifact.training_window if artifact else {},
            "training_metadata": artifact.training_metadata if artifact else {},
        }

    def _predict(self, *, asset_id: str | None) -> list[dict[str, Any]]:
        artifact = self._ensure_artifact()
        features = self.prepare_features(
            asset_id=asset_id,
            limit=100_000 if asset_id is None else 50_000,
            resample=True,
        )
        if features.empty:
            return []

        latest = (
            features.sort_values(["asset_id", "time"], kind="mergesort")
            .groupby("asset_id", as_index=False)
            .tail(1)
            .reset_index(drop=True)
        )
        probabilities, contributions = predict_xgboost(artifact, latest)
        generated_at = datetime.now(timezone.utc)

        output: list[dict[str, Any]] = []
        for row_index, (_, row) in enumerate(latest.iterrows()):
            probability = float(probabilities[row_index])
            output.append(
                {
                    "asset_id": str(row["asset_id"]),
                    "asset_name": str(row.get("asset_name") or row["asset_id"]),
                    "affected_zone": str(row.get("zone") or ""),
                    "maintenance_risk": round(probability, 4),
                    "risk_level": self._risk_level(probability),
                    "confidence": round(0.5 + abs(probability - 0.5), 4),
                    "predicted_failure_days": self._remaining_useful_life(probability),
                    "top_contributing_features": self._top_contributors(
                        artifact.feature_names,
                        contributions[row_index],
                    ),
                    "generated_at": generated_at,
                    "model_version": artifact.model_version,
                }
            )
        return sorted(output, key=lambda item: item["maintenance_risk"], reverse=True)

    def _ensure_artifact(self) -> XGBoostArtifact:
        if self._artifact is not None:
            return self._artifact
        artifact = load_artifact(PUMP_XGBOOST_PATH)
        if artifact is None:
            raise ValidationAppError(
                "XGBoost model not trained. Call POST "
                "/api/predictions/maintenance/train before requesting predictions."
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
    def _remaining_useful_life(probability: float) -> int:
        """
        Demo RUL approximation, not a separately trained regression model.

        Maps model risk monotonically to 1–180 days:
        ``round(180 * (1-risk)^2)``, floored at one day.
        """
        return max(1, min(180, round(180.0 * (1.0 - probability) ** 2)))

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
