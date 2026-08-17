"""Build cleaned, resampled maintenance DataFrames from live pump telemetry."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlalchemy.orm import Session

from app.ml.common import clean_nulls, resample_mean, rows_to_frame, sort_by_time
from app.ml.common.constants import DEFAULT_RESAMPLE_FREQ, MAINTENANCE_METRIC_KEYS
from app.ml.maintenance.loader import load_asset_context, load_maintenance_telemetry


class MaintenanceDatasetBuilder:
    """
    Construct per-asset pump telemetry panels for future XGBoost training.

    Source of truth: PostgreSQL telemetry produced by the water simulator.
    """

    TARGET_METRICS = MAINTENANCE_METRIC_KEYS

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
        Return a long/wide panel with columns::

            time, asset_id, flow_rate, pressure, pump_speed, pump_running,
            health, status, zone, criticality, scenario_intensity
        """
        rows = load_maintenance_telemetry(
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
            return pd.DataFrame(
                columns=[
                    "time",
                    "asset_id",
                    "asset_name",
                    *self.TARGET_METRICS,
                    "health",
                    "status",
                    "zone",
                    "criticality",
                    "scenario_intensity",
                ]
            )

        panels: list[pd.DataFrame] = []
        for aid, group in raw.groupby("asset_id"):
            wide = self._pivot_asset(group, resample=resample)
            if wide.empty:
                continue
            ctx = load_asset_context(self._db, str(aid)) or {}
            wide["asset_id"] = str(aid)
            wide["asset_name"] = ctx.get("asset_name", str(aid))
            wide["health"] = float(ctx.get("health", 0.0))
            wide["status"] = ctx.get("status", "unknown")
            wide["zone"] = ctx.get("zone", "")
            wide["criticality"] = ctx.get("criticality", "medium")
            wide["scenario_intensity"] = float(ctx.get("scenario_intensity", 0.0))
            panels.append(wide)

        if not panels:
            return pd.DataFrame(
                columns=[
                    "time",
                    "asset_id",
                    "asset_name",
                    *self.TARGET_METRICS,
                    "health",
                    "status",
                    "zone",
                    "criticality",
                    "scenario_intensity",
                ]
            )

        out = pd.concat(panels, ignore_index=True)
        out = out.sort_values(["asset_id", "time"], kind="mergesort").reset_index(drop=True)
        return out

    def _pivot_asset(self, group: pd.DataFrame, *, resample: bool) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for metric in self.TARGET_METRICS:
            subset = group[group["metric_key"] == metric][["recorded_at", "value"]]
            if subset.empty:
                continue
            subset = sort_by_time(subset)
            if resample:
                subset = resample_mean(
                    subset, freq=self._resample_freq, value_col="value"
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
        merged = merged.rename(columns={"recorded_at": "time"})
        return merged.reset_index(drop=True)
