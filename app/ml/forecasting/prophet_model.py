"""
Prophet demand forecasting model (Sprint 6B).

Training / inference use ONLY real PostgreSQL telemetry prepared by
``ForecastDatasetBuilder.build_demand_series`` — no CSV, no synthetic history.

Pipeline::

    ForecastDatasetBuilder (demand series)
         ↓
    to_prophet_frame (ds, y)
         ↓
    train / save → app/ml/artifacts/prophet_water.pkl
         ↓
    load / predict → future demand with confidence intervals
"""

from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from app.ml.artifacts import PROPHET_WATER_PATH
from app.ml.common.constants import DEFAULT_RESAMPLE_FREQ, MODEL_VERSION_PROPHET

logger = logging.getLogger(__name__)


@dataclass
class ProphetArtifact:
    """Persisted Prophet model plus training metadata."""

    model: Any
    model_version: str
    trained_at: datetime
    training_window: dict[str, Any]
    demand_source: dict[str, Any]
    resample_freq: str
    zone_id: str | None = None


def to_prophet_frame(demand_series: pd.DataFrame) -> pd.DataFrame:
    """
    Convert ``[time, demand]`` from the dataset builder into Prophet ``[ds, y]``.

    Drops nulls; sorts by ``ds``. Uses real values only.
    """
    if demand_series is None or demand_series.empty:
        return pd.DataFrame(columns=["ds", "y"])

    frame = demand_series.copy()
    if "time" not in frame.columns or "demand" not in frame.columns:
        raise ValueError("Demand series must include columns: time, demand")

    frame = frame[["time", "demand"]].dropna()
    frame["ds"] = pd.to_datetime(frame["time"], utc=True).dt.tz_localize(None)
    frame["y"] = pd.to_numeric(frame["demand"], errors="coerce")
    frame = frame.dropna(subset=["ds", "y"])
    frame = frame.sort_values("ds", kind="mergesort").reset_index(drop=True)
    # Prophet requires unique timestamps.
    frame = frame.groupby("ds", as_index=False)["y"].mean()
    return frame[["ds", "y"]]


def _build_prophet(*, points: int) -> Any:
    """Configure Prophet for short / medium telemetry windows."""
    from prophet import Prophet

    # With < ~2 days of history, weekly/yearly seasonality is not identifiable.
    daily = points >= 24  # roughly ≥2h at 5min, or ≥1d at hourly
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


def train_prophet(
    demand_series: pd.DataFrame,
    *,
    demand_source: dict[str, Any] | None = None,
    resample_freq: str = DEFAULT_RESAMPLE_FREQ,
    zone_id: str | None = None,
    artifact_path: Path | None = None,
) -> ProphetArtifact:
    """Fit Prophet on a real demand series and persist the artifact."""
    frame = to_prophet_frame(demand_series)
    if len(frame) < 10:
        raise ValueError(
            f"Insufficient telemetry for Prophet training: {len(frame)} points "
            "(need at least 10 resampled observations from PostgreSQL)."
        )

    model = _build_prophet(points=len(frame))
    logger.info("Training Prophet on %s demand points", len(frame))
    model.fit(frame)

    trained_at = datetime.now(timezone.utc)
    artifact = ProphetArtifact(
        model=model,
        model_version=MODEL_VERSION_PROPHET,
        trained_at=trained_at,
        training_window={
            "start": frame["ds"].iloc[0].to_pydatetime().replace(tzinfo=timezone.utc),
            "end": frame["ds"].iloc[-1].to_pydatetime().replace(tzinfo=timezone.utc),
            "rows": int(len(frame)),
            "freq": resample_freq,
        },
        demand_source=demand_source or {},
        resample_freq=resample_freq,
        zone_id=zone_id,
    )
    save_artifact(artifact, path=artifact_path)
    return artifact


def save_artifact(artifact: ProphetArtifact, *, path: Path | None = None) -> Path:
    path = path or PROPHET_WATER_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": artifact.model,
        "model_version": artifact.model_version,
        "trained_at": artifact.trained_at,
        "training_window": artifact.training_window,
        "demand_source": artifact.demand_source,
        "resample_freq": artifact.resample_freq,
        "zone_id": artifact.zone_id,
    }
    with path.open("wb") as fh:
        pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
    logger.info("Saved Prophet artifact → %s", path)
    return path


def load_artifact(path: Path | None = None) -> ProphetArtifact | None:
    path = path or PROPHET_WATER_PATH
    if not path.exists():
        return None
    with path.open("rb") as fh:
        payload = pickle.load(fh)
    return ProphetArtifact(
        model=payload["model"],
        model_version=str(payload.get("model_version", MODEL_VERSION_PROPHET)),
        trained_at=_as_utc(payload.get("trained_at")),
        training_window=dict(payload.get("training_window") or {}),
        demand_source=dict(payload.get("demand_source") or {}),
        resample_freq=str(payload.get("resample_freq") or DEFAULT_RESAMPLE_FREQ),
        zone_id=payload.get("zone_id"),
    )


def predict_prophet(
    artifact: ProphetArtifact,
    *,
    hours: int = 48,
) -> pd.DataFrame:
    """
    Forecast ``hours`` ahead at the artifact's training frequency.

    Returns columns: ``ds, yhat, yhat_lower, yhat_upper``.
    """
    if hours < 1:
        raise ValueError("hours must be >= 1")

    freq = artifact.resample_freq or DEFAULT_RESAMPLE_FREQ
    periods = _periods_for_hours(hours, freq)
    future = artifact.model.make_future_dataframe(periods=periods, freq=freq)
    forecast = artifact.model.predict(future)

    # Keep only future horizon (after last training timestamp).
    history_end = artifact.training_window.get("end")
    if history_end is not None:
        end_ts = pd.Timestamp(history_end).tz_localize(None)
        forecast = forecast[forecast["ds"] > end_ts]

    out = forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]].copy()
    # Physical floor: flow cannot be negative.
    for col in ("yhat", "yhat_lower", "yhat_upper"):
        out[col] = out[col].clip(lower=0.0)
    return out.reset_index(drop=True)


def _periods_for_hours(hours: int, freq: str) -> int:
    """Convert horizon hours into Prophet periods for a pandas offset freq."""
    delta = pd.tseries.frequencies.to_offset(freq)
    if delta is None:
        return hours * 12  # assume 5min
    # Number of freq steps in `hours`.
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
    "ProphetArtifact",
    "load_artifact",
    "predict_prophet",
    "save_artifact",
    "to_prophet_frame",
    "train_prophet",
]
