"""
Gas demand forecast service — Prophet over PostgreSQL Gas telemetry.

Independent from Water/Power forecasting and from Gas XGBoost PdM.
Does not mint operational recommendations from forecast existence alone.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationAppError
from app.domain.repositories import ZoneRepository
from app.ml.artifacts import PROPHET_GAS_DEMAND_PATH
from app.ml.gas.forecast_constants import (
    DEFAULT_FORECAST_HOURS,
    GAS_DEMAND_METRIC_KEY,
    GAS_DEMAND_UNIT,
    MODEL_NAME,
    MODEL_VERSION_GAS_PROPHET,
)
from app.ml.gas.forecast_dataset import GasDemandDatasetBuilder
from app.ml.gas.prophet_model import (
    GasProphetArtifact,
    load_artifact,
    predict_gas_prophet,
    resolve_forecast_request,
    train_gas_prophet,
)


class GasForecastService:
    """Corridor / zone Gas flow_rate_nm3_h demand forecasting."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._datasets = GasDemandDatasetBuilder(db)
        self._artifact: GasProphetArtifact | None = None

    def preparation_summary(
        self,
        *,
        zone_id: str | None = None,
        limit: int = 100_000,
    ) -> dict[str, Any]:
        series, meta = self._datasets.build_demand_series(zone_id=zone_id, limit=limit)
        return {
            "status": "prepared",
            "model": MODEL_NAME,
            "utility_kind": "gas",
            "target_metric": GAS_DEMAND_METRIC_KEY,
            "artifact_path": str(PROPHET_GAS_DEMAND_PATH),
            "artifact_exists": PROPHET_GAS_DEMAND_PATH.exists(),
            "rows": int(len(series)),
            "scope": meta.get("scope"),
            "asset_type": meta.get("asset_type"),
            "resample_freq": meta.get("resample_freq"),
            "limited_history": meta.get("limited_history"),
            "time_start": series["time"].iloc[0].isoformat() if len(series) else None,
            "time_end": series["time"].iloc[-1].isoformat() if len(series) else None,
            "inference": "available"
            if PROPHET_GAS_DEMAND_PATH.exists()
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

        series, meta = self._datasets.build_demand_series(
            zone_id=zone_id,
            start=start,
            end=end,
            limit=limit,
            resample=True,
        )
        if series.empty:
            raise ValidationAppError(
                "No Gas flow_rate_nm3_h telemetry available in PostgreSQL for "
                "Prophet training. Run the Gas simulator first."
            )

        freq = str(meta.get("resample_freq") or "1s")
        try:
            artifact = train_gas_prophet(
                series,
                demand_source={
                    "utility_kind": "gas",
                    "asset_type": meta.get("asset_type"),
                    "asset_ids": meta.get("asset_ids"),
                    "metric_key": meta.get("metric_key"),
                    "aggregation": meta.get("aggregation"),
                    "scope": meta.get("scope"),
                    "unit": GAS_DEMAND_UNIT,
                    "source": "postgresql_gas_telemetry",
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
            "utility_kind": "gas",
            "target_metric": GAS_DEMAND_METRIC_KEY,
            "assessment_kind": "demand_forecast_model",
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
        series, meta = self._datasets.build_demand_series(
            zone_id=zone_id,
            limit=100_000,
            resample=True,
        )
        current = float(series["demand"].iloc[-1]) if len(series) else None
        current_at = series["time"].iloc[-1] if len(series) else None

        request = resolve_forecast_request(artifact, hours=max(1, hours))
        if request["mode"] == "periods":
            raw = predict_gas_prophet(artifact, periods=int(request["periods"]))
        else:
            raw = predict_gas_prophet(artifact, hours=int(request["hours"]))

        points = self._points_payload(raw)
        recent = self._recent_actual(series, limit=48)

        return {
            "utility_kind": "gas",
            "model_name": MODEL_NAME,
            "model_version": artifact.model_version,
            "provider": "prophet",
            "assessment_kind": "demand_forecast_model",
            "target_metric": GAS_DEMAND_METRIC_KEY,
            "unit": GAS_DEMAND_UNIT,
            "status": "ok",
            "source": "model_forecast",
            "current_demand": current,
            "current_demand_at": current_at,
            "forecast_horizon": request["horizon_label"],
            "forecast_hours": request.get("hours"),
            "limited_history": bool(request.get("limited_history")),
            "model_available": True,
            "generated_at": datetime.now(timezone.utc),
            "training_window": _serialize_window(artifact.training_window),
            "demand_source": artifact.demand_source
            or {
                "asset_ids": meta.get("asset_ids"),
                "asset_type": meta.get("asset_type"),
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
                    "Short observed history — period-based horizon used. "
                    "Simulator-derived POC; not plant-certified."
                    if request.get("limited_history")
                    else (
                        "POC Gas demand forecast from Prophet over simulator "
                        "telemetry; not plant-certified."
                    )
                ),
            },
        }

    def demand_forecast(self, *, zone_id: str | None = None) -> dict[str, Any]:
        return self.predict(zone_id=zone_id, hours=DEFAULT_FORECAST_HOURS)

    def status(self, *, zone_id: str | None = None) -> dict[str, Any]:
        path = self._artifact_path_for_zone(zone_id)
        artifact = load_artifact(path)
        if artifact is None and zone_id is not None:
            artifact = load_artifact(PROPHET_GAS_DEMAND_PATH)
            path = PROPHET_GAS_DEMAND_PATH
        exists = artifact is not None
        return {
            "model": MODEL_NAME,
            "model_version": artifact.model_version if artifact else None,
            "utility_kind": "gas",
            "assessment_kind": "demand_forecast_model",
            "target_metric": GAS_DEMAND_METRIC_KEY,
            "provider": "prophet",
            "artifact_exists": exists,
            "artifact_path": path.name,
            "inference": "available" if exists else "train_required",
            "status": "available" if exists else "unavailable",
            "last_training_time": artifact.trained_at if artifact else None,
            "training_window": _serialize_window(artifact.training_window)
            if artifact
            else {},
            "demand_source": artifact.demand_source if artifact else {},
            "expected_model_version": MODEL_VERSION_GAS_PROPHET,
            "zone_id": zone_id,
            "limited_history": bool((artifact.training_metadata or {}).get("limited_history"))
            if artifact
            else None,
        }

    def _ensure_artifact(self, *, zone_id: str | None) -> GasProphetArtifact:
        path = self._artifact_path_for_zone(zone_id)
        if self._artifact is not None and (
            zone_id is None or self._artifact.zone_id == zone_id
        ):
            return self._artifact

        artifact = load_artifact(path)
        if artifact is None and zone_id is not None:
            artifact = load_artifact(PROPHET_GAS_DEMAND_PATH)
        if artifact is None:
            raise ValidationAppError(
                "Gas Prophet demand model not trained. Call POST "
                "/api/predictions/gas/forecast/train before requesting forecasts."
            )
        self._artifact = artifact
        return artifact

    @staticmethod
    def _artifact_path_for_zone(zone_id: str | None):
        if zone_id:
            return PROPHET_GAS_DEMAND_PATH.with_name(f"prophet_gas_demand_{zone_id}.pkl")
        return PROPHET_GAS_DEMAND_PATH

    @staticmethod
    def _points_payload(forecast: pd.DataFrame) -> list[dict[str, Any]]:
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
                    "kind": "forecast",
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
                    "kind": "actual",
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
