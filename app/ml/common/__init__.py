"""Shared ML helpers — time series cleaning and resampling over real telemetry."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd


def rows_to_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Convert telemetry row dicts to a DataFrame; empty-safe."""
    if not rows:
        return pd.DataFrame(
            columns=["recorded_at", "value", "metric_key", "asset_id", "unit", "quality"]
        )
    frame = pd.DataFrame(rows)
    if "recorded_at" in frame.columns:
        frame["recorded_at"] = pd.to_datetime(frame["recorded_at"], utc=True)
    return frame


def clean_nulls(frame: pd.DataFrame, *, value_col: str = "value") -> pd.DataFrame:
    """Drop rows with null timestamps or null/NaN values."""
    if frame.empty:
        return frame.copy()
    out = frame.dropna(subset=["recorded_at", value_col]).copy()
    out = out[pd.to_numeric(out[value_col], errors="coerce").notna()]
    out[value_col] = out[value_col].astype(float)
    return out


def sort_by_time(frame: pd.DataFrame) -> pd.DataFrame:
    """Ascending chronological order."""
    if frame.empty or "recorded_at" not in frame.columns:
        return frame.copy()
    return frame.sort_values("recorded_at", kind="mergesort").reset_index(drop=True)


def resample_mean(
    frame: pd.DataFrame,
    *,
    freq: str = "5min",
    value_col: str = "value",
) -> pd.DataFrame:
    """
    Resample a single-series frame to a fixed frequency (mean aggregation).

    Expects columns ``recorded_at`` and ``value_col``. Returns columns
    ``recorded_at`` and ``value``.
    """
    if frame.empty:
        return pd.DataFrame(columns=["recorded_at", "value"])

    series = frame.set_index("recorded_at")[value_col].astype(float)
    resampled = series.resample(freq).mean().dropna()
    out = resampled.reset_index()
    out.columns = ["recorded_at", "value"]
    return out


def ensure_utc_bounds(
    start: datetime | None,
    end: datetime | None,
) -> tuple[datetime | None, datetime | None]:
    """Normalize optional bounds to timezone-aware UTC."""

    def _aware(ts: datetime | None) -> datetime | None:
        if ts is None:
            return None
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)

    return _aware(start), _aware(end)


__all__ = [
    "clean_nulls",
    "ensure_utc_bounds",
    "resample_mean",
    "rows_to_frame",
    "sort_by_time",
]
