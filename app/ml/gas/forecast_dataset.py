"""Build corridor / zone Gas demand series from PostgreSQL telemetry."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.ml.common import clean_nulls, resample_mean, rows_to_frame, sort_by_time
from app.ml.gas.forecast_constants import (
    FALLBACK_RESAMPLE_FREQ,
    GAS_DEMAND_AGGREGATION,
    GAS_DEMAND_METRIC_KEY,
    MIN_SPAN_FOR_HOUR_HORIZON,
    PREFERRED_RESAMPLE_FREQ,
)
from app.ml.gas.forecast_loader import load_gas_demand_telemetry


class GasDemandDatasetBuilder:
    """
    Construct a single Gas demand series for Prophet.

    Series definition (corridor, default)::

        y = sum(flow_rate_nm3_h) across preferred Gas demand assets
            (gas_feeder → gas_regulator_station → gas_compressor)

    Optional ``zone_id`` restricts the sum to that zone's preferred layer.
    Zone C / Zone D assets are included when present — no zone drop.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def build_demand_series(
        self,
        *,
        zone_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100_000,
        resample: bool = True,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        asset_type, asset_ids, rows = load_gas_demand_telemetry(
            self._db,
            zone_id=zone_id,
            start=start,
            end=end,
            limit=limit,
        )
        meta: dict[str, Any] = {
            "utility_kind": "gas",
            "asset_type": asset_type,
            "asset_ids": asset_ids,
            "metric_key": GAS_DEMAND_METRIC_KEY,
            "aggregation": GAS_DEMAND_AGGREGATION,
            "zone_id": zone_id,
            "scope": f"zone:{zone_id}" if zone_id else "corridor",
            "row_count_raw": len(rows),
            "resample_freq": None,
            "limited_history": False,
        }
        raw = rows_to_frame(rows)
        raw = clean_nulls(raw)
        raw = sort_by_time(raw)
        if raw.empty:
            return pd.DataFrame(columns=["time", "demand"]), meta

        # Align dense simulator ticks (ms differences) to whole seconds.
        raw = raw.copy()
        raw["recorded_at"] = pd.to_datetime(raw["recorded_at"], utc=True).dt.floor("s")

        if GAS_DEMAND_AGGREGATION == "sum":
            per_ts = (
                raw.groupby("recorded_at", as_index=False)["value"]
                .sum()
                .rename(columns={"value": "demand"})
            )
        else:
            per_ts = (
                raw.groupby("recorded_at", as_index=False)["value"]
                .mean()
                .rename(columns={"value": "demand"})
            )
        per_ts = sort_by_time(per_ts.rename(columns={"demand": "value"}))
        per_ts = per_ts.rename(columns={"value": "demand"})

        freq = self._choose_freq(per_ts)
        meta["resample_freq"] = freq if resample else None
        meta["observed_span_seconds"] = _span_seconds(per_ts)

        if resample:
            narrow = per_ts.rename(columns={"demand": "value"})
            series = resample_mean(narrow, freq=freq, value_col="value")
            series = series.rename(columns={"value": "demand", "recorded_at": "time"})
        else:
            series = per_ts.rename(columns={"recorded_at": "time"})

        if "time" not in series.columns and "recorded_at" in series.columns:
            series = series.rename(columns={"recorded_at": "time"})

        span = _span_seconds(
            series.rename(columns={"time": "recorded_at"})
            if "time" in series.columns
            else series
        )
        meta["limited_history"] = span < MIN_SPAN_FOR_HOUR_HORIZON.total_seconds()
        meta["row_count"] = len(series)
        if len(series):
            meta["time_start"] = series["time"].iloc[0]
            meta["time_end"] = series["time"].iloc[-1]
        return series[["time", "demand"]].reset_index(drop=True), meta

    @staticmethod
    def _choose_freq(per_ts: pd.DataFrame) -> str:
        span = _span_seconds(per_ts)
        if span >= MIN_SPAN_FOR_HOUR_HORIZON.total_seconds():
            return PREFERRED_RESAMPLE_FREQ
        return FALLBACK_RESAMPLE_FREQ


def _span_seconds(frame: pd.DataFrame) -> float:
    if frame is None or frame.empty:
        return 0.0
    col = "recorded_at" if "recorded_at" in frame.columns else "time"
    if col not in frame.columns or len(frame) < 2:
        return 0.0
    times = pd.to_datetime(frame[col], utc=True, errors="coerce").dropna()
    if len(times) < 2:
        return 0.0
    return float((times.max() - times.min()).total_seconds())


__all__ = ["GasDemandDatasetBuilder"]
