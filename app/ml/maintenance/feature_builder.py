"""Maintenance feature engineering for predictive-maintenance models."""

from __future__ import annotations

import pandas as pd

from app.ml.common.constants import DEFAULT_ROLLING_WINDOW


class MaintenanceFeatureBuilder:
    """
    Derive PdM features from a maintenance dataset panel.

    Output includes::

        rolling mean / std / min / max / variance
        pressure / flow slopes and rates of change
        pump utilization and operating duration
        pump_speed, pump_running
        health, status, zone, criticality, latest scenario intensity
        time, asset_id
    """

    def __init__(self, *, window: int = DEFAULT_ROLLING_WINDOW) -> None:
        self._window = max(2, window)

    def build(self, dataset: pd.DataFrame) -> pd.DataFrame:
        empty_cols = [
            "time",
            "asset_id",
            "asset_name",
            "pressure",
            "flow_rate",
            "pump_speed",
            "pump_running",
            "pressure_rolling_mean",
            "pressure_rolling_std",
            "pressure_rolling_min",
            "pressure_rolling_max",
            "pressure_variance",
            "flow_rolling_mean",
            "flow_rolling_std",
            "flow_rolling_min",
            "flow_rolling_max",
            "flow_variance",
            "pump_speed_rolling_mean",
            "pump_speed_rolling_std",
            "pressure_slope",
            "flow_slope",
            "pressure_rate_of_change",
            "flow_rate_of_change",
            "pump_utilization",
            "operating_duration_minutes",
            "health",
            "status",
            "zone",
            "criticality",
            "scenario_intensity",
        ]
        if dataset is None or dataset.empty:
            return pd.DataFrame(columns=empty_cols)

        parts: list[pd.DataFrame] = []
        for asset_id, group in dataset.groupby("asset_id", sort=False):
            parts.append(self._features_for_asset(group.copy()))

        if not parts:
            return pd.DataFrame(columns=empty_cols)

        out = pd.concat(parts, ignore_index=True)
        return out.sort_values(["asset_id", "time"], kind="mergesort").reset_index(drop=True)

    def _features_for_asset(self, frame: pd.DataFrame) -> pd.DataFrame:
        frame = frame.sort_values("time", kind="mergesort").reset_index(drop=True)
        frame["time"] = pd.to_datetime(frame["time"], utc=True)
        w = self._window

        for col in ("pressure", "flow_rate", "pump_speed", "pump_running"):
            if col not in frame.columns:
                frame[col] = pd.NA
            frame[col] = pd.to_numeric(frame[col], errors="coerce")

        frame["pressure_rolling_mean"] = frame["pressure"].rolling(w, min_periods=1).mean()
        frame["pressure_rolling_std"] = frame["pressure"].rolling(w, min_periods=1).std()
        frame["pressure_rolling_min"] = frame["pressure"].rolling(w, min_periods=1).min()
        frame["pressure_rolling_max"] = frame["pressure"].rolling(w, min_periods=1).max()
        frame["pressure_variance"] = frame["pressure"].rolling(w, min_periods=1).var()
        frame["flow_rolling_mean"] = frame["flow_rate"].rolling(w, min_periods=1).mean()
        frame["flow_rolling_std"] = frame["flow_rate"].rolling(w, min_periods=1).std()
        frame["flow_rolling_min"] = frame["flow_rate"].rolling(w, min_periods=1).min()
        frame["flow_rolling_max"] = frame["flow_rate"].rolling(w, min_periods=1).max()
        frame["flow_variance"] = frame["flow_rate"].rolling(w, min_periods=1).var()
        frame["pump_speed_rolling_mean"] = (
            frame["pump_speed"].rolling(w, min_periods=1).mean()
        )
        frame["pump_speed_rolling_std"] = frame["pump_speed"].rolling(w, min_periods=1).std()

        # Slopes are per resampled observation; rates normalize by elapsed minute.
        frame["pressure_slope"] = frame["pressure"].diff().fillna(0.0)
        frame["flow_slope"] = frame["flow_rate"].diff().fillna(0.0)
        elapsed_minutes = frame["time"].diff().dt.total_seconds().div(60.0)
        elapsed_minutes = elapsed_minutes.where(elapsed_minutes > 0)
        frame["pressure_rate_of_change"] = (
            frame["pressure"].diff().div(elapsed_minutes).fillna(0.0)
        )
        frame["flow_rate_of_change"] = (
            frame["flow_rate"].diff().div(elapsed_minutes).fillna(0.0)
        )

        running = frame["pump_running"].fillna(0.0).ge(0.5)
        frame["pump_utilization"] = (
            running.astype(float).rolling(w, min_periods=1).mean()
        )
        median_step = elapsed_minutes.dropna().median()
        step_minutes = float(median_step) if pd.notna(median_step) else 5.0
        run_group = running.ne(running.shift()).cumsum()
        consecutive = running.groupby(run_group).cumcount().add(1)
        frame["operating_duration_minutes"] = consecutive.where(running, 0) * step_minutes

        for col in (
            "health",
            "status",
            "zone",
            "criticality",
            "asset_id",
            "asset_name",
            "time",
            "scenario_intensity",
        ):
            if col not in frame.columns:
                frame[col] = pd.NA

        cols = [
            "time",
            "asset_id",
            "asset_name",
            "pressure",
            "flow_rate",
            "pump_speed",
            "pump_running",
            "pressure_rolling_mean",
            "pressure_rolling_std",
            "pressure_rolling_min",
            "pressure_rolling_max",
            "pressure_variance",
            "flow_rolling_mean",
            "flow_rolling_std",
            "flow_rolling_min",
            "flow_rolling_max",
            "flow_variance",
            "pump_speed_rolling_mean",
            "pump_speed_rolling_std",
            "pressure_slope",
            "flow_slope",
            "pressure_rate_of_change",
            "flow_rate_of_change",
            "pump_utilization",
            "operating_duration_minutes",
            "health",
            "status",
            "zone",
            "criticality",
            "scenario_intensity",
        ]
        return frame[cols]
