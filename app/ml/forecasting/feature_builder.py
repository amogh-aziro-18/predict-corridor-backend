"""Forecast feature engineering — no model inference (Sprint 6A)."""

from __future__ import annotations

import pandas as pd


class ForecastFeatureBuilder:
    """
    Derive forecasting features from a cleaned dataset.

    Output columns (when present)::

        time, hour, day, flow_rate, pressure, reservoir_level
    """

    def build(self, dataset: pd.DataFrame) -> pd.DataFrame:
        if dataset is None or dataset.empty:
            return pd.DataFrame(
                columns=["time", "hour", "day", "flow_rate", "pressure", "reservoir_level"]
            )

        frame = dataset.copy()
        if "time" not in frame.columns and "recorded_at" in frame.columns:
            frame = frame.rename(columns={"recorded_at": "time"})

        frame["time"] = pd.to_datetime(frame["time"], utc=True)
        frame["hour"] = frame["time"].dt.hour.astype(int)
        frame["day"] = frame["time"].dt.dayofweek.astype(int)

        for col in ("flow_rate", "pressure", "reservoir_level"):
            if col not in frame.columns:
                frame[col] = pd.NA

        return frame[
            ["time", "hour", "day", "flow_rate", "pressure", "reservoir_level"]
        ].reset_index(drop=True)
