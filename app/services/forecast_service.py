"""
Forecast service — Prophet demand forecasting (Sprint 6B).

Orchestrates::

    Simulator → Telemetry (PostgreSQL)
         ↓
    ForecastDatasetBuilder.build_demand_series
         ↓
    ForecastFeatureBuilder (diagnostics)
         ↓
    prophet_model.train / predict
         ↓
    GET /api/forecasts/demand

Training is explicit via ``train()`` — never automatic on each request.
Inference loads ``app/ml/artifacts/prophet_water.pkl`` when available.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationAppError
from app.domain.repositories import ZoneRepository
from app.ml.artifacts import PROPHET_WATER_PATH
from app.ml.common.constants import DEFAULT_RESAMPLE_FREQ
from app.ml.forecasting.dataset import ForecastDatasetBuilder
from app.ml.forecasting.feature_builder import ForecastFeatureBuilder
from app.ml.forecasting.prophet_model import (
    ProphetArtifact,
    load_artifact,
    predict_prophet,
    train_prophet,
)


class ForecastService:
    """Demand forecast orchestration over real PostgreSQL telemetry."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._datasets = ForecastDatasetBuilder(db)
        self._features = ForecastFeatureBuilder()
        self._artifact: ProphetArtifact | None = None

    # ------------------------------------------------------------------
    # Sprint 6A helpers (unchanged behaviour)
    # ------------------------------------------------------------------

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
        features = self.prepare_features(
            asset_id=asset_id,
            start=start,
            end=end,
            limit=limit,
        )
        return {
            "status": "prepared",
            "model": "prophet_water",
            "artifact_path": str(PROPHET_WATER_PATH),
            "artifact_exists": PROPHET_WATER_PATH.exists(),
            "rows": int(len(features)),
            "columns": list(features.columns),
            "time_start": features["time"].iloc[0].isoformat() if len(features) else None,
            "time_end": features["time"].iloc[-1].isoformat() if len(features) else None,
            "inference": "available" if PROPHET_WATER_PATH.exists() else "train_required",
        }

    # ------------------------------------------------------------------
    # Sprint 6B — train / predict
    # ------------------------------------------------------------------

    def train(
        self,
        *,
        zone_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100_000,
    ) -> dict[str, Any]:
        """
        Train Prophet from PostgreSQL demand telemetry and save the artifact.

        Does not run automatically on API forecast requests.
        """
        if zone_id is not None and ZoneRepository(self._db).get(zone_id) is None:
            raise NotFoundError(f"Zone not found: {zone_id}")

        series, meta = self._datasets.build_demand_series(
            zone_id=zone_id,
            start=start,
            end=end,
            limit=limit,
            resample=True,
        )
        if series.empty:
            raise ValidationAppError(
                "No demand telemetry available in PostgreSQL for Prophet training. "
                "Ensure the water simulator has written flow_rate history."
            )

        artifact = train_prophet(
            series,
            demand_source={
                "asset_type": meta.get("asset_type"),
                "asset_ids": meta.get("asset_ids"),
                "metric_key": meta.get("metric_key"),
                "aggregation": meta.get("aggregation"),
            },
            resample_freq=DEFAULT_RESAMPLE_FREQ,
            zone_id=zone_id,
            artifact_path=self._artifact_path_for_zone(zone_id),
        )
        self._artifact = artifact
        return {
            "status": "trained",
            "model_version": artifact.model_version,
            "artifact_path": str(self._artifact_path_for_zone(zone_id)),
            "training_window": _serialize_window(artifact.training_window),
            "demand_source": artifact.demand_source,
            "rows": artifact.training_window.get("rows"),
            "trained_at": artifact.trained_at.isoformat(),
        }

    def predict(
        self,
        *,
        zone_id: str | None = None,
        hours: int = 48,
    ) -> dict[str, Any]:
        """
        Forecast demand for the next ``hours`` (default 48).

        Loads the saved artifact if present; does not retrain.
        """
        artifact = self._ensure_artifact(zone_id=zone_id)

        # Current demand = latest resampled observation from PostgreSQL.
        # Use the same limit as training so ASC history queries are not truncated
        # to an outdated early window.
        series, meta = self._datasets.build_demand_series(
            zone_id=zone_id,
            limit=100_000,
            resample=True,
        )
        current_demand = float(series["demand"].iloc[-1]) if len(series) else None
        current_at = series["time"].iloc[-1] if len(series) else None

        raw = predict_prophet(artifact, hours=max(hours, 48))
        history_end = artifact.training_window.get("end")
        hourly = self._to_hourly(raw, history_end=history_end)

        horizon_24 = hourly[hourly["hours_ahead"] <= 24].copy()
        horizon_48 = hourly[hourly["hours_ahead"] <= 48].copy()

        confidence = self._confidence_summary(horizon_48)

        return {
            "current_demand": current_demand,
            "current_demand_at": current_at,
            "unit": "L/s",
            "forecast": {
                "next_24_hours": self._points_payload(horizon_24),
                "next_48_hours": self._points_payload(horizon_48),
            },
            "confidence": confidence,
            "generated_at": datetime.now(timezone.utc),
            "model_version": artifact.model_version,
            "training_window": _serialize_window(artifact.training_window),
            "demand_source": artifact.demand_source or {
                "asset_type": meta.get("asset_type"),
                "asset_ids": meta.get("asset_ids"),
                "metric_key": meta.get("metric_key"),
                "aggregation": meta.get("aggregation"),
            },
            "zone_id": zone_id,
            "recent_actual": self._recent_actual_hourly(series, hours=24),
        }

    def demand_forecast(self, *, zone_id: str | None = None) -> dict[str, Any]:
        """Public façade used by API routers."""
        return self.predict(zone_id=zone_id, hours=48)

    def status(self, *, zone_id: str | None = None) -> dict[str, Any]:
        """Artifact readiness without running inference."""
        path = self._artifact_path_for_zone(zone_id)
        artifact = load_artifact(path)
        if artifact is None and zone_id is not None:
            artifact = load_artifact(PROPHET_WATER_PATH)
            path = PROPHET_WATER_PATH
        exists = artifact is not None
        return {
            "model": "prophet_demand",
            "model_version": artifact.model_version if artifact else None,
            "artifact_exists": exists,
            "artifact_path": str(path),
            "inference": "available" if exists else "train_required",
            "last_training_time": artifact.trained_at if artifact else None,
            "training_window": _serialize_window(artifact.training_window)
            if artifact
            else {},
            "demand_source": artifact.demand_source if artifact else {},
            "zone_id": zone_id,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ensure_artifact(self, *, zone_id: str | None) -> ProphetArtifact:
        path = self._artifact_path_for_zone(zone_id)
        if self._artifact is not None and (
            zone_id is None or self._artifact.zone_id == zone_id
        ):
            return self._artifact

        artifact = load_artifact(path)
        if artifact is None and zone_id is not None:
            # Fall back to corridor-wide model when zone-specific missing.
            artifact = load_artifact(PROPHET_WATER_PATH)
        if artifact is None:
            raise ValidationAppError(
                "Prophet model not trained. Call ForecastService.train() or "
                "POST /api/forecasts/demand/train before requesting forecasts."
            )
        self._artifact = artifact
        return artifact

    @staticmethod
    def _artifact_path_for_zone(zone_id: str | None):
        if zone_id:
            return PROPHET_WATER_PATH.with_name(f"prophet_water_{zone_id}.pkl")
        return PROPHET_WATER_PATH

    @staticmethod
    def _to_hourly(
        forecast: pd.DataFrame,
        *,
        history_end: Any = None,
    ) -> pd.DataFrame:
        if forecast.empty:
            return pd.DataFrame(
                columns=["timestamp", "yhat", "yhat_lower", "yhat_upper", "hours_ahead"]
            )
        frame = forecast.copy()
        frame["ds"] = pd.to_datetime(frame["ds"], utc=True)
        frame = frame.set_index("ds")
        hourly = frame[["yhat", "yhat_lower", "yhat_upper"]].resample("1h").mean().dropna()
        if history_end is not None:
            end_ts = pd.Timestamp(history_end)
            if end_ts.tzinfo is None:
                end_ts = end_ts.tz_localize("UTC")
            else:
                end_ts = end_ts.tz_convert("UTC")
            # Keep hour buckets that end after the training window.
            hourly = hourly[hourly.index > end_ts.floor("h")]
        hourly = hourly.reset_index()
        if hourly.empty:
            return pd.DataFrame(
                columns=["timestamp", "yhat", "yhat_lower", "yhat_upper", "hours_ahead"]
            )
        origin = hourly["ds"].iloc[0]
        hourly["hours_ahead"] = (
            ((hourly["ds"] - origin) / pd.Timedelta(hours=1)).round().astype(int) + 1
        )
        hourly = hourly.rename(columns={"ds": "timestamp"})
        return hourly

    @staticmethod
    def _points_payload(frame: pd.DataFrame) -> list[dict[str, Any]]:
        points: list[dict[str, Any]] = []
        for _, row in frame.iterrows():
            ts = row["timestamp"]
            if getattr(ts, "tzinfo", None) is None:
                ts = pd.Timestamp(ts).tz_localize("UTC")
            else:
                ts = pd.Timestamp(ts).tz_convert("UTC")
            points.append(
                {
                    "timestamp": ts.to_pydatetime(),
                    "hours_ahead": int(row["hours_ahead"]),
                    "demand": round(float(row["yhat"]), 2),
                    "lower": round(float(row["yhat_lower"]), 2),
                    "upper": round(float(row["yhat_upper"]), 2),
                }
            )
        return points

    @staticmethod
    def _confidence_summary(frame: pd.DataFrame) -> dict[str, Any]:
        if frame.empty:
            return {
                "interval_width": 0.80,
                "mean_interval_pct": None,
                "level": "unknown",
            }
        mid = frame["yhat"].replace(0, pd.NA)
        width = (frame["yhat_upper"] - frame["yhat_lower"]).abs()
        pct = (width / mid).dropna()
        mean_pct = float(pct.mean()) if len(pct) else None
        if mean_pct is None:
            level = "unknown"
        elif mean_pct < 0.15:
            level = "high"
        elif mean_pct < 0.35:
            level = "medium"
        else:
            level = "low"
        return {
            "interval_width": 0.80,
            "mean_interval_pct": round(mean_pct, 4) if mean_pct is not None else None,
            "level": level,
            "note": "80% Prophet uncertainty interval; short history may lower confidence",
        }

    @staticmethod
    def _recent_actual_hourly(series: pd.DataFrame, *, hours: int = 24) -> list[dict[str, Any]]:
        """Hourly mean of real demand series for charting (no fabricated points)."""
        if series is None or series.empty or "time" not in series.columns:
            return []
        frame = series.copy()
        frame["time"] = pd.to_datetime(frame["time"], utc=True)
        frame = frame.set_index("time")[["demand"]].astype(float)
        hourly = frame.resample("1h").mean().dropna()
        if hourly.empty:
            return []
        end = hourly.index.max()
        cutoff = end - pd.Timedelta(hours=max(hours, 1))
        hourly = hourly[hourly.index >= cutoff]
        points: list[dict[str, Any]] = []
        for ts, row in hourly.iterrows():
            stamp = pd.Timestamp(ts)
            if stamp.tzinfo is None:
                stamp = stamp.tz_localize("UTC")
            else:
                stamp = stamp.tz_convert("UTC")
            points.append(
                {
                    "timestamp": stamp.to_pydatetime(),
                    "demand": round(float(row["demand"]), 2),
                }
            )
        return points


def _serialize_window(window: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in (window or {}).items():
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            out[key] = value.isoformat()
        elif hasattr(value, "isoformat"):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out
