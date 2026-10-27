"""
Power load forecast service — Prophet over PostgreSQL Power telemetry.

Independent from Water demand forecasting and from transformer XGBoost PdM.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationAppError
from app.domain.repositories import ZoneRepository
from app.ml.artifacts import PROPHET_POWER_LOAD_PATH
from app.ml.power.forecast_constants import (
    DEFAULT_FORECAST_HOURS,
    MODEL_NAME,
    MODEL_VERSION_POWER_PROPHET,
    POWER_LOAD_METRIC_KEY,
    POWER_LOAD_UNIT,
)
from app.ml.power.forecast_dataset import PowerLoadDatasetBuilder
from app.ml.power.prophet_model import (
    PowerProphetArtifact,
    load_artifact,
    predict_power_prophet,
    resolve_forecast_request,
    train_power_prophet,
)


class PowerForecastService:
    """Corridor / zone Power active_power_mw forecasting."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._datasets = PowerLoadDatasetBuilder(db)
        self._artifact: PowerProphetArtifact | None = None

    def preparation_summary(
        self,
        *,
        zone_id: str | None = None,
        limit: int = 100_000,
    ) -> dict[str, Any]:
        series, meta = self._datasets.build_load_series(zone_id=zone_id, limit=limit)
        return {
            "status": "prepared",
            "model": MODEL_NAME,
            "utility_kind": "power",
            "target_metric": POWER_LOAD_METRIC_KEY,
            "artifact_path": str(PROPHET_POWER_LOAD_PATH),
            "artifact_exists": PROPHET_POWER_LOAD_PATH.exists(),
            "rows": int(len(series)),
            "scope": meta.get("scope"),
            "resample_freq": meta.get("resample_freq"),
            "limited_history": meta.get("limited_history"),
            "time_start": series["time"].iloc[0].isoformat() if len(series) else None,
            "time_end": series["time"].iloc[-1].isoformat() if len(series) else None,
            "inference": "available"
            if PROPHET_POWER_LOAD_PATH.exists()
            else "train_required",
        }

    def train(
        self,
        *,
        zone_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100_000,
    ) -> dict[str, Any]:
        if zone_id is not None and ZoneRepository(self._db).get(zone_id) is None:
            raise NotFoundError(f"Zone not found: {zone_id}")

        series, meta = self._datasets.build_load_series(
            zone_id=zone_id,
            start=start,
            end=end,
            limit=limit,
            resample=True,
        )
        if series.empty:
            raise ValidationAppError(
                "No Power active_power_mw telemetry available in PostgreSQL for "
                "Prophet training. Run the Power simulator first."
            )

        freq = str(meta.get("resample_freq") or "1s")
        try:
            artifact = train_power_prophet(
                series,
                demand_source={
                    "utility_kind": "power",
                    "asset_ids": meta.get("asset_ids"),
                    "metric_key": meta.get("metric_key"),
                    "aggregation": meta.get("aggregation"),
                    "scope": meta.get("scope"),
                },
                resample_freq=freq,
                zone_id=zone_id,
                artifact_path=self._artifact_path_for_zone(zone_id),
            )
        except ValueError as exc:
            raise ValidationAppError(str(exc)) from exc

        self._artifact = artifact
        return {
            "status": "trained",
            "model": MODEL_NAME,
            "model_version": artifact.model_version,
            "utility_kind": "power",
            "target_metric": POWER_LOAD_METRIC_KEY,
            "assessment_kind": "load_forecast_model",
            "artifact_path": self._artifact_path_for_zone(zone_id).name,
            "training_window": _serialize_window(artifact.training_window),
            "demand_source": artifact.demand_source,
            "rows": artifact.training_window.get("rows"),
            "trained_at": artifact.trained_at,
            "resample_freq": artifact.resample_freq,
            "validation_metrics": (artifact.training_metadata or {}).get(
                "validation_metrics", {}
            ),
            "validation_note": (artifact.training_metadata or {}).get("validation_note"),
            "limited_history": (artifact.training_metadata or {}).get(
                "limited_history", False
            ),
            "zone_id": zone_id,
        }

    def predict(
        self,
        *,
        zone_id: str | None = None,
        hours: int = DEFAULT_FORECAST_HOURS,
    ) -> dict[str, Any]:
        artifact = self._ensure_artifact(zone_id=zone_id)
        series, meta = self._datasets.build_load_series(
            zone_id=zone_id,
            limit=100_000,
            resample=True,
        )
        current = float(series["demand"].iloc[-1]) if len(series) else None
        current_at = series["time"].iloc[-1] if len(series) else None

        request = resolve_forecast_request(artifact, hours=max(1, hours))
        if request["mode"] == "periods":
            raw = predict_power_prophet(artifact, periods=int(request["periods"]))
        else:
            raw = predict_power_prophet(artifact, hours=int(request["hours"]))

        points = self._points_payload(raw, history_end=artifact.training_window.get("end"))
        recent = self._recent_actual(series, limit=48)

        return {
            "utility_kind": "power",
            "model_name": MODEL_NAME,
            "model_version": artifact.model_version,
            "assessment_kind": "load_forecast_model",
            "target_metric": POWER_LOAD_METRIC_KEY,
            "unit": POWER_LOAD_UNIT,
            "current_load": current,
            "current_load_at": current_at,
            "forecast_horizon": request["horizon_label"],
            "forecast_hours": request.get("hours"),
            "limited_history": bool(request.get("limited_history")),
            "model_available": True,
            "generated_at": datetime.now(timezone.utc),
            "training_window": _serialize_window(artifact.training_window),
            "demand_source": artifact.demand_source
            or {
                "asset_ids": meta.get("asset_ids"),
                "metric_key": meta.get("metric_key"),
                "aggregation": meta.get("aggregation"),
                "scope": meta.get("scope"),
            },
            "zone_id": zone_id,
            "forecast_points": points,
            "recent_actual": recent,
            "confidence": {
                "interval_width": 0.80,
                "level": "limited" if request.get("limited_history") else "poc",
                "note": (
                    "Short observed history — period-based horizon used."
                    if request.get("limited_history")
                    else "POC Power load forecast from Prophet; not plant-certified."
                ),
            },
        }

    def load_forecast(self, *, zone_id: str | None = None) -> dict[str, Any]:
        return self.predict(zone_id=zone_id, hours=DEFAULT_FORECAST_HOURS)

    def status(self, *, zone_id: str | None = None) -> dict[str, Any]:
        path = self._artifact_path_for_zone(zone_id)
        artifact = load_artifact(path)
        if artifact is None and zone_id is not None:
            artifact = load_artifact(PROPHET_POWER_LOAD_PATH)
            path = PROPHET_POWER_LOAD_PATH
        exists = artifact is not None
        return {
            "model": MODEL_NAME,
            "model_version": artifact.model_version if artifact else None,
            "utility_kind": "power",
            "assessment_kind": "load_forecast_model",
            "target_metric": POWER_LOAD_METRIC_KEY,
            "artifact_exists": exists,
            "artifact_path": path.name,
            "inference": "available" if exists else "train_required",
            "last_training_time": artifact.trained_at if artifact else None,
            "training_window": _serialize_window(artifact.training_window)
            if artifact
            else {},
            "demand_source": artifact.demand_source if artifact else {},
            "expected_model_version": MODEL_VERSION_POWER_PROPHET,
            "zone_id": zone_id,
            "limited_history": bool((artifact.training_metadata or {}).get("limited_history"))
            if artifact
            else None,
        }

    def _ensure_artifact(self, *, zone_id: str | None) -> PowerProphetArtifact:
        path = self._artifact_path_for_zone(zone_id)
        if self._artifact is not None and (
            zone_id is None or self._artifact.zone_id == zone_id
        ):
            return self._artifact

        artifact = load_artifact(path)
        if artifact is None and zone_id is not None:
            artifact = load_artifact(PROPHET_POWER_LOAD_PATH)
        if artifact is None:
            raise ValidationAppError(
                "Power Prophet load model not trained. Call POST "
                "/api/predictions/power/forecast/train before requesting forecasts."
            )
        self._artifact = artifact
        return artifact

    @staticmethod
    def _artifact_path_for_zone(zone_id: str | None):
        if zone_id:
            return PROPHET_POWER_LOAD_PATH.with_name(f"prophet_power_load_{zone_id}.pkl")
        return PROPHET_POWER_LOAD_PATH

    @staticmethod
    def _points_payload(
        forecast: pd.DataFrame,
        *,
        history_end: Any = None,
    ) -> list[dict[str, Any]]:
        if forecast.empty:
            return []
        frame = forecast.copy()
        frame["ds"] = pd.to_datetime(frame["ds"], utc=True)
        origin = frame["ds"].iloc[0]
        points: list[dict[str, Any]] = []
        for _, row in frame.iterrows():
            ahead = (row["ds"] - origin) / pd.Timedelta(hours=1)
            points.append(
                {
                    "timestamp": row["ds"].to_pydatetime(),
                    "hours_ahead": float(round(float(ahead), 4)),
                    "value": round(float(row["yhat"]), 4),
                    "lower": round(float(row["yhat_lower"]), 4),
                    "upper": round(float(row["yhat_upper"]), 4),
                }
            )
        return points

    @staticmethod
    def _recent_actual(series: pd.DataFrame, *, limit: int = 48) -> list[dict[str, Any]]:
        if series is None or series.empty:
            return []
        tail = series.tail(limit)
        out: list[dict[str, Any]] = []
        for _, row in tail.iterrows():
            ts = pd.Timestamp(row["time"])
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            out.append(
                {
                    "timestamp": ts.to_pydatetime(),
                    "value": round(float(row["demand"]), 4),
                }
            )
        return out


def _serialize_window(window: dict[str, Any] | None) -> dict[str, Any]:
    if not window:
        return {}
    out: dict[str, Any] = dict(window)
    for key in ("start", "end"):
        value = out.get(key)
        if isinstance(value, datetime):
            out[key] = value.isoformat()
        elif hasattr(value, "isoformat"):
            out[key] = value.isoformat()
    return out
