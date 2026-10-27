"""
Prophet Power load forecasting model (Phase 14.3C2).

Training / inference use ONLY real PostgreSQL Power telemetry prepared by
``PowerLoadDatasetBuilder.build_load_series`` — no CSV, no synthetic history
in the production path.

Pipeline::

    PowerLoadDatasetBuilder (corridor / zone load)
         ↓
    to_prophet_frame (ds, y)
         ↓
    train / save → app/ml/artifacts/prophet_power_load.pkl
         ↓
    load / predict → future active_power_mw with intervals
"""

from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.ml.artifacts import PROPHET_POWER_LOAD_PATH
from app.ml.power.forecast_constants import (
    DEFAULT_FORECAST_HOURS,
    FALLBACK_RESAMPLE_FREQ,
    HOLDOUT_FRACTION,
    MIN_HOLDOUT_POINTS,
    MIN_SPAN_FOR_HOUR_HORIZON,
    MIN_TRAINING_POINTS,
    MODEL_VERSION_POWER_PROPHET,
    SHORT_HISTORY_FORECAST_PERIODS,
)

logger = logging.getLogger(__name__)


@dataclass
class PowerProphetArtifact:
    """Persisted Power Prophet model plus training metadata."""

    model: Any
    model_version: str
    trained_at: datetime
    training_window: dict[str, Any]
    demand_source: dict[str, Any]
    resample_freq: str
    zone_id: str | None = None
    training_metadata: dict[str, Any] | None = None


def to_prophet_frame(load_series: pd.DataFrame) -> pd.DataFrame:
    """Convert ``[time, demand]`` into Prophet ``[ds, y]`` (real values only)."""
    if load_series is None or load_series.empty:
        return pd.DataFrame(columns=["ds", "y"])

    frame = load_series.copy()
    if "time" not in frame.columns or "demand" not in frame.columns:
        raise ValueError("Load series must include columns: time, demand")

    frame = frame[["time", "demand"]].dropna()
    frame["ds"] = pd.to_datetime(frame["time"], utc=True).dt.tz_localize(None)
    frame["y"] = pd.to_numeric(frame["demand"], errors="coerce")
    frame = frame.dropna(subset=["ds", "y"])
    frame = frame.sort_values("ds", kind="mergesort").reset_index(drop=True)
    frame = frame.groupby("ds", as_index=False)["y"].mean()
    return frame[["ds", "y"]]


def _build_prophet(*, points: int) -> Any:
    from prophet import Prophet

    daily = points >= 24
    model = Prophet(
        daily_seasonality=daily,
        weekly_seasonality=False,
        yearly_seasonality=False,
        changepoint_prior_scale=0.05,
        seasonality_mode="additive",
        interval_width=0.80,
    )
    if points >= 12:
        model.add_seasonality(name="hourly", period=1 / 24, fourier_order=3)
    return model


def train_power_prophet(
    load_series: pd.DataFrame,
    *,
    demand_source: dict[str, Any] | None = None,
    resample_freq: str = FALLBACK_RESAMPLE_FREQ,
    zone_id: str | None = None,
    artifact_path: Path | None = None,
) -> PowerProphetArtifact:
    """Fit Prophet on a real Power load series and persist the artifact."""
    frame = to_prophet_frame(load_series)
    if len(frame) < MIN_TRAINING_POINTS:
        raise ValueError(
            f"Insufficient Power telemetry for Prophet training: {len(frame)} points "
            f"(need at least {MIN_TRAINING_POINTS} observations from PostgreSQL)."
        )

    validation_metrics, validation_note = _chronological_backtest(
        frame, resample_freq=resample_freq
    )

    model = _build_prophet(points=len(frame))
    logger.info("Training Power Prophet on %s load points", len(frame))
    model.fit(frame)

    span_seconds = 0.0
    if len(frame) >= 2:
        span_seconds = float((frame["ds"].iloc[-1] - frame["ds"].iloc[0]).total_seconds())
    limited = span_seconds < MIN_SPAN_FOR_HOUR_HORIZON.total_seconds()

    trained_at = datetime.now(timezone.utc)
    artifact = PowerProphetArtifact(
        model=model,
        model_version=MODEL_VERSION_POWER_PROPHET,
        trained_at=trained_at,
        training_window={
            "start": frame["ds"].iloc[0].to_pydatetime().replace(tzinfo=timezone.utc),
            "end": frame["ds"].iloc[-1].to_pydatetime().replace(tzinfo=timezone.utc),
            "rows": int(len(frame)),
            "freq": resample_freq,
            "span_seconds": span_seconds,
        },
        demand_source=demand_source or {},
        resample_freq=resample_freq,
        zone_id=zone_id,
        training_metadata={
            "model_name": "power_load_forecast",
            "utility_kind": "power",
            "target_metric": "active_power_mw",
            "assessment_kind": "load_forecast_model",
            "limited_history": limited,
            "default_forecast_hours": DEFAULT_FORECAST_HOURS,
            "validation_metrics": validation_metrics,
            "validation_note": validation_note,
            "prophet": {
                "daily_seasonality": len(frame) >= 24,
                "weekly_seasonality": False,
                "yearly_seasonality": False,
                "interval_width": 0.80,
                "changepoint_prior_scale": 0.05,
            },
        },
    )
    save_artifact(artifact, path=artifact_path)
    return artifact


def save_artifact(artifact: PowerProphetArtifact, *, path: Path | None = None) -> Path:
    path = path or PROPHET_POWER_LOAD_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": artifact.model,
        "model_version": artifact.model_version,
        "trained_at": artifact.trained_at,
        "training_window": artifact.training_window,
        "demand_source": artifact.demand_source,
        "resample_freq": artifact.resample_freq,
        "zone_id": artifact.zone_id,
        "training_metadata": artifact.training_metadata or {},
    }
    with path.open("wb") as fh:
        pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
    logger.info("Saved Power Prophet artifact → %s", path)
    return path


def load_artifact(path: Path | None = None) -> PowerProphetArtifact | None:
    path = path or PROPHET_POWER_LOAD_PATH
    if not path.exists():
        return None
    with path.open("rb") as fh:
        payload = pickle.load(fh)
    return PowerProphetArtifact(
        model=payload["model"],
        model_version=str(payload.get("model_version", MODEL_VERSION_POWER_PROPHET)),
        trained_at=_as_utc(payload.get("trained_at")),
        training_window=dict(payload.get("training_window") or {}),
        demand_source=dict(payload.get("demand_source") or {}),
        resample_freq=str(payload.get("resample_freq") or FALLBACK_RESAMPLE_FREQ),
        zone_id=payload.get("zone_id"),
        training_metadata=dict(payload.get("training_metadata") or {}),
    )


def predict_power_prophet(
    artifact: PowerProphetArtifact,
    *,
    hours: int | None = None,
    periods: int | None = None,
) -> pd.DataFrame:
    """
    Forecast ahead at the artifact's training frequency.

    Prefer ``hours`` when history supports hour-scale horizons; otherwise pass
    ``periods`` for a short step-based horizon. Returns ``ds, yhat, yhat_lower,
    yhat_upper`` for future timestamps only.
    """
    freq = artifact.resample_freq or FALLBACK_RESAMPLE_FREQ
    if periods is not None:
        n_periods = max(1, int(periods))
    else:
        horizon_hours = hours if hours is not None else DEFAULT_FORECAST_HOURS
        if horizon_hours < 1:
            raise ValueError("hours must be >= 1")
        n_periods = _periods_for_hours(horizon_hours, freq)

    future = artifact.model.make_future_dataframe(periods=n_periods, freq=freq)
    forecast = artifact.model.predict(future)

    history_end = artifact.training_window.get("end")
    if history_end is not None:
        end_ts = pd.Timestamp(history_end).tz_localize(None)
        forecast = forecast[forecast["ds"] > end_ts]

    out = forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]].copy()
    for col in ("yhat", "yhat_lower", "yhat_upper"):
        out[col] = out[col].clip(lower=0.0)
    return out.reset_index(drop=True)


def resolve_forecast_request(
    artifact: PowerProphetArtifact,
    *,
    hours: int = DEFAULT_FORECAST_HOURS,
) -> dict[str, Any]:
    """Decide hour-based vs period-based horizon from training span."""
    meta = artifact.training_metadata or {}
    limited = bool(meta.get("limited_history"))
    span = float((artifact.training_window or {}).get("span_seconds") or 0.0)
    if limited or span < MIN_SPAN_FOR_HOUR_HORIZON.total_seconds():
        return {
            "mode": "periods",
            "periods": SHORT_HISTORY_FORECAST_PERIODS,
            "hours": None,
            "horizon_label": (
                f"{SHORT_HISTORY_FORECAST_PERIODS} steps @ {artifact.resample_freq}"
            ),
            "limited_history": True,
        }
    return {
        "mode": "hours",
        "periods": None,
        "hours": hours,
        "horizon_label": f"{hours}h @ {artifact.resample_freq}",
        "limited_history": False,
    }


def _chronological_backtest(
    frame: pd.DataFrame,
    *,
    resample_freq: str,
) -> tuple[dict[str, Any], str]:
    """Hold out the last chronological fraction; no random shuffle / no leakage."""
    n = len(frame)
    holdout = max(MIN_HOLDOUT_POINTS, int(n * HOLDOUT_FRACTION))
    if n < MIN_TRAINING_POINTS + MIN_HOLDOUT_POINTS:
        return {}, "insufficient history for chronological holdout"

    split = n - holdout
    train = frame.iloc[:split].copy()
    test = frame.iloc[split:].copy()
    if len(train) < MIN_TRAINING_POINTS:
        return {}, "insufficient train split for chronological holdout"

    model = _build_prophet(points=len(train))
    model.fit(train)
    future = model.make_future_dataframe(periods=len(test), freq=resample_freq)
    forecast = model.predict(future)
    end_train = train["ds"].iloc[-1]
    pred = forecast[forecast["ds"] > end_train][["ds", "yhat"]].reset_index(drop=True)
    # Align by position (chronological) — lengths should match.
    k = min(len(pred), len(test))
    if k == 0:
        return {}, "no overlapping holdout predictions"
    y_true = test["y"].to_numpy(dtype=float)[:k]
    y_hat = pred["yhat"].to_numpy(dtype=float)[:k]
    metrics = _forecast_metrics(y_true, y_hat)
    metrics["holdout_rows"] = int(k)
    metrics["train_rows"] = int(len(train))
    return metrics, "chronological holdout"


def _forecast_metrics(y_true: np.ndarray, y_hat: np.ndarray) -> dict[str, Any]:
    err = y_hat - y_true
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err**2)))
    denom = np.where(np.abs(y_true) > 1e-6, np.abs(y_true), np.nan)
    mape = float(np.nanmean(np.abs(err) / denom) * 100.0)
    return {
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
        "mape_pct": round(mape, 4) if not np.isnan(mape) else None,
    }


def _periods_for_hours(hours: int, freq: str) -> int:
    delta = pd.tseries.frequencies.to_offset(freq)
    if delta is None:
        return hours * 60  # assume 1min
    steps = pd.Timedelta(hours=hours) / pd.Timedelta(delta)
    return max(1, int(round(steps)))


def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if value is None:
        return datetime.now(timezone.utc)
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.to_pydatetime().replace(tzinfo=timezone.utc)
    return ts.tz_convert("UTC").to_pydatetime()


__all__ = [
    "PowerProphetArtifact",
    "load_artifact",
    "predict_power_prophet",
    "resolve_forecast_request",
    "save_artifact",
    "to_prophet_frame",
    "train_power_prophet",
]
