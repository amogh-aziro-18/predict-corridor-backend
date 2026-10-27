"""Build cleaned, resampled transformer DataFrames from live Power telemetry."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlalchemy.orm import Session

from app.ml.common import clean_nulls, resample_mean, rows_to_frame, sort_by_time
from app.ml.power.constants import (
    DEFAULT_RESAMPLE_ENABLED,
    DEFAULT_RESAMPLE_FREQ,
    TRANSFORMER_METRIC_KEYS,
)
from app.ml.power.loader import load_transformer_context, load_transformer_telemetry


class TransformerDatasetBuilder:
    """
    Construct per-asset Power transformer panels for XGBoost training/inference.

    Source of truth: PostgreSQL telemetry produced by the Power simulator.
    """

    TARGET_METRICS = TRANSFORMER_METRIC_KEYS

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
        resample: bool = DEFAULT_RESAMPLE_ENABLED,
    ) -> pd.DataFrame:
        rows = load_transformer_telemetry(
            self._db,
            asset_id=asset_id,
            start=start,
            end=end,
            limit=limit,
        )
        raw = rows_to_frame(rows)
        raw = clean_nulls(raw)
        raw = sort_by_time(raw)
        empty_cols = [
            "time",
            "asset_id",
            "asset_name",
            *self.TARGET_METRICS,
            "zone",
            "criticality",
            "nominal_voltage_kv",
            "utility_kind",
        ]
        if raw.empty:
            return pd.DataFrame(columns=empty_cols)

        panels: list[pd.DataFrame] = []
        for aid, group in raw.groupby("asset_id"):
            wide = self._pivot_asset(group, resample=resample)
            if wide.empty:
                continue
            ctx = load_transformer_context(self._db, str(aid)) or {}
            wide["asset_id"] = str(aid)
            wide["asset_name"] = ctx.get("asset_name", str(aid))
            wide["zone"] = ctx.get("zone", "")
            wide["criticality"] = ctx.get("criticality", "medium")
            wide["nominal_voltage_kv"] = float(ctx.get("nominal_voltage_kv", 132.0))
            wide["utility_kind"] = "power"
            panels.append(wide)

        if not panels:
            return pd.DataFrame(columns=empty_cols)

        out = pd.concat(panels, ignore_index=True)
        return out.sort_values(["asset_id", "time"], kind="mergesort").reset_index(drop=True)

    def _pivot_asset(self, group: pd.DataFrame, *, resample: bool) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for metric in self.TARGET_METRICS:
            subset = group[group["metric_key"] == metric][["recorded_at", "value"]].copy()
            if subset.empty:
                continue
            subset = sort_by_time(subset)
            # Align dense simulator ticks that differ by milliseconds.
            subset["recorded_at"] = pd.to_datetime(subset["recorded_at"], utc=True).dt.floor(
                "s"
            )
            if resample:
                subset = resample_mean(
                    subset, freq=self._resample_freq, value_col="value"
                )
            else:
                # One value per second per metric (last write wins).
                subset = (
                    subset.sort_values("recorded_at", kind="mergesort")
                    .groupby("recorded_at", as_index=False)
                    .agg({"value": "last"})
                )
            subset = subset.rename(columns={"value": metric})
            frames.append(subset)

        if not frames:
            return pd.DataFrame()

        merged = frames[0]
        for extra in frames[1:]:
            merged = pd.merge(merged, extra, on="recorded_at", how="outer")
        merged = merged.sort_values("recorded_at", kind="mergesort")
        for col in self.TARGET_METRICS:
            if col not in merged.columns:
                merged[col] = pd.NA
            merged[col] = merged[col].ffill().bfill()
        merged = merged.rename(columns={"recorded_at": "time"})
        return merged.reset_index(drop=True)


__all__ = ["TransformerDatasetBuilder"]
