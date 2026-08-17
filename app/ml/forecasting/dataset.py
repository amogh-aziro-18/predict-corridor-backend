"""Build cleaned, resampled forecast DataFrames from live telemetry."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.ml.common import clean_nulls, resample_mean, rows_to_frame, sort_by_time
from app.ml.common.constants import (
    DEFAULT_RESAMPLE_FREQ,
    DEMAND_AGGREGATION,
    DEMAND_METRIC_KEY,
    FORECAST_METRIC_KEYS,
)
from app.ml.forecasting.loader import load_demand_telemetry, load_forecast_telemetry


class ForecastDatasetBuilder:
    """
    Construct time-indexed datasets for Prophet training / diagnostics.

    Reads PostgreSQL telemetry only — never synthetic CSV or hardcoded series.
    """

    TARGET_METRICS = FORECAST_METRIC_KEYS

    def __init__(
        self,
        db: Session,
        *,
        resample_freq: str = DEFAULT_RESAMPLE_FREQ,
    ) -> None:
        self._db = db
        self._resample_freq = resample_freq

    def build(
        self,
        *,
        asset_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 50_000,
        resample: bool = True,
    ) -> pd.DataFrame:
        """
        Return a DataFrame with columns among::

            time, flow_rate, pressure, reservoir_level
        """
        rows = load_forecast_telemetry(
            self._db,
            asset_id=asset_id,
            start=start,
            end=end,
            limit=limit,
        )
        raw = rows_to_frame(rows)
        raw = clean_nulls(raw)
        raw = sort_by_time(raw)
        if raw.empty:
            return pd.DataFrame(columns=["time", *self.TARGET_METRICS])

        frames: list[pd.DataFrame] = []
        for metric in self.TARGET_METRICS:
            subset = raw[raw["metric_key"] == metric][["recorded_at", "value", "asset_id"]]
            if subset.empty:
                continue
            per_ts = (
                subset.groupby("recorded_at", as_index=False)["value"]
                .mean()
                .rename(columns={"value": metric})
            )
            per_ts = sort_by_time(per_ts.rename(columns={metric: "value"}))
            per_ts = per_ts.rename(columns={"value": metric})
            if resample:
                narrow = per_ts.rename(columns={metric: "value"})
                narrow = resample_mean(
                    narrow, freq=self._resample_freq, value_col="value"
                )
                narrow = narrow.rename(columns={"value": metric})
                frames.append(narrow)
            else:
                frames.append(per_ts)

        if not frames:
            return pd.DataFrame(columns=["time", *self.TARGET_METRICS])

        merged = frames[0]
        for extra in frames[1:]:
            merged = pd.merge(merged, extra, on="recorded_at", how="outer")

        merged = merged.sort_values("recorded_at", kind="mergesort").reset_index(drop=True)
        merged = merged.rename(columns={"recorded_at": "time"})
        for col in self.TARGET_METRICS:
            if col not in merged.columns:
                merged[col] = pd.NA
        merged = merged[["time", *self.TARGET_METRICS]]
        merged = merged.dropna(how="all", subset=list(self.TARGET_METRICS))
        return merged.reset_index(drop=True)

    def build_demand_series(
        self,
        *,
        zone_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100_000,
        resample: bool = True,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """
        Build a single demand series from preferred flow assets.

        Priority: flow_meter → distribution_network → pump_station.
        Aggregation: sum of ``flow_rate`` across selected assets per timestamp,
        then optional resample.

        Returns ``(DataFrame[time, demand], metadata)``.
        """
        asset_type, asset_ids, rows = load_demand_telemetry(
            self._db,
            zone_id=zone_id,
            start=start,
            end=end,
            limit=limit,
        )
        meta: dict[str, Any] = {
            "asset_type": asset_type,
            "asset_ids": asset_ids,
            "metric_key": DEMAND_METRIC_KEY,
            "aggregation": DEMAND_AGGREGATION,
            "resample_freq": self._resample_freq if resample else None,
            "zone_id": zone_id,
            "row_count_raw": len(rows),
        }
        raw = rows_to_frame(rows)
        raw = clean_nulls(raw)
        raw = sort_by_time(raw)
        if raw.empty:
            return pd.DataFrame(columns=["time", "demand"]), meta

        if DEMAND_AGGREGATION == "sum":
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

        if resample:
            narrow = per_ts.rename(columns={"demand": "value"})
            narrow = resample_mean(narrow, freq=self._resample_freq, value_col="value")
            series = narrow.rename(columns={"value": "demand", "recorded_at": "time"})
        else:
            series = per_ts.rename(columns={"recorded_at": "time"})

        if "time" not in series.columns and "recorded_at" in series.columns:
            series = series.rename(columns={"recorded_at": "time"})

        meta["row_count"] = len(series)
        if len(series):
            meta["time_start"] = series["time"].iloc[0]
            meta["time_end"] = series["time"].iloc[-1]
        return series[["time", "demand"]].reset_index(drop=True), meta
