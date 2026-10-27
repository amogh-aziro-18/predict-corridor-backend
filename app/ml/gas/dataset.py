"""Build cleaned Gas DataFrames from live Gas telemetry."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlalchemy.orm import Session

from app.ml.common import clean_nulls, resample_mean, rows_to_frame, sort_by_time
from app.ml.gas.constants import (
    DEFAULT_RESAMPLE_ENABLED,
    DEFAULT_RESAMPLE_FREQ,
    GAS_METRIC_KEYS,
)
from app.ml.gas.loader import load_gas_context, load_gas_telemetry


class GasDatasetBuilder:
    """
    Construct per-asset Gas panels for XGBoost training/inference.

    Source of truth: PostgreSQL telemetry produced by the Gas simulator.
    """

    TARGET_METRICS = GAS_METRIC_KEYS

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
        rows = load_gas_telemetry(
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
            "asset_type",
            "nominal_pressure_bar",
            "nominal_flow_nm3_h",
            "nominal_discharge_pressure_bar",
            "nominal_power_kw",
            "utility_kind",
        ]
        if raw.empty:
            return pd.DataFrame(columns=empty_cols)

        panels: list[pd.DataFrame] = []
        for aid, group in raw.groupby("asset_id"):
            wide = self._pivot_asset(group, resample=resample)
            if wide.empty:
                continue
            ctx = load_gas_context(self._db, str(aid)) or {}
            wide["asset_id"] = str(aid)
            wide["asset_name"] = ctx.get("asset_name", str(aid))
            wide["zone"] = ctx.get("zone", "")
            wide["criticality"] = ctx.get("criticality", "medium")
            wide["asset_type"] = ctx.get("asset_type", "gas_feeder")
            wide["nominal_pressure_bar"] = float(ctx.get("nominal_pressure_bar", 28.0))
            wide["nominal_flow_nm3_h"] = float(ctx.get("nominal_flow_nm3_h", 2000.0))
            wide["nominal_discharge_pressure_bar"] = float(
                ctx.get("nominal_discharge_pressure_bar", 32.0)
            )
            wide["nominal_power_kw"] = float(ctx.get("nominal_power_kw", 380.0))
            wide["utility_kind"] = "gas"
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
            subset["recorded_at"] = pd.to_datetime(subset["recorded_at"], utc=True).dt.floor(
                "s"
            )
            if resample:
                subset = resample_mean(
                    subset, freq=self._resample_freq, value_col="value"
                )
            else:
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
            # Forward/back fill within the asset's available metrics only.
            merged[col] = merged[col].ffill().bfill()
        merged = merged.rename(columns={"recorded_at": "time"})
        return merged.reset_index(drop=True)


__all__ = ["GasDatasetBuilder"]
