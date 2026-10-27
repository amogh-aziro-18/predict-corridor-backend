"""Transformer feature engineering for Power predictive-maintenance models."""

from __future__ import annotations

import pandas as pd

from app.ml.power.constants import DEFAULT_ROLLING_WINDOW, TRANSFORMER_METRIC_KEYS

# Explicit, versioned feature contract for transformer_xgboost_v1.
# Excludes: zone, scenario name, condition label, health score (leakage / shortcut).
FEATURE_COLUMNS: tuple[str, ...] = (
    "time",
    "asset_id",
    "asset_name",
    "utility_kind",
    "zone",  # carried for output context only — not used as model input
    "criticality",
    "nominal_voltage_kv",
    "oil_temp_c",
    "load_pct",
    "voltage_kv",
    "active_power_mw",
    "power_factor",
    "oil_temp_rolling_mean",
    "oil_temp_rolling_std",
    "oil_temp_slope",
    "load_rolling_mean",
    "load_rolling_std",
    "load_slope",
    "voltage_rolling_mean",
    "voltage_rolling_std",
    "voltage_slope",
    "power_factor_rolling_mean",
    "power_factor_slope",
    "active_power_rolling_mean",
    "active_power_slope",
    "voltage_deviation_kv",
    "load_stress",
    "oil_temp_delta",
    "power_factor_deviation",
)

MODEL_NUMERIC_FEATURES: tuple[str, ...] = (
    "oil_temp_c",
    "load_pct",
    "voltage_kv",
    "active_power_mw",
    "power_factor",
    "oil_temp_rolling_mean",
    "oil_temp_rolling_std",
    "oil_temp_slope",
    "load_rolling_mean",
    "load_rolling_std",
    "load_slope",
    "voltage_rolling_mean",
    "voltage_rolling_std",
    "voltage_slope",
    "power_factor_rolling_mean",
    "power_factor_slope",
    "active_power_rolling_mean",
    "active_power_slope",
    "voltage_deviation_kv",
    "load_stress",
    "oil_temp_delta",
    "power_factor_deviation",
    "nominal_voltage_kv",
)

# Only asset criticality — not zone (avoids learning zone==C/D shortcuts).
MODEL_CATEGORICAL_FEATURES: tuple[str, ...] = ("criticality",)


class TransformerFeatureBuilder:
    """
    Derive PdM features from a transformer dataset panel.

    Temporal features use a rolling window over chronologically ordered samples.
    """

    def __init__(self, *, window: int = DEFAULT_ROLLING_WINDOW) -> None:
        self._window = max(2, window)

    def build(self, dataset: pd.DataFrame) -> pd.DataFrame:
        if dataset is None or dataset.empty:
            return pd.DataFrame(columns=list(FEATURE_COLUMNS))

        parts: list[pd.DataFrame] = []
        for _asset_id, group in dataset.groupby("asset_id", sort=False):
            parts.append(self._features_for_asset(group.copy()))

        if not parts:
            return pd.DataFrame(columns=list(FEATURE_COLUMNS))

        out = pd.concat(parts, ignore_index=True)
        return out.sort_values(["asset_id", "time"], kind="mergesort").reset_index(drop=True)

    def _features_for_asset(self, frame: pd.DataFrame) -> pd.DataFrame:
        frame = frame.sort_values("time", kind="mergesort").reset_index(drop=True)
        frame["time"] = pd.to_datetime(frame["time"], utc=True)
        w = self._window

        for col in TRANSFORMER_METRIC_KEYS:
            if col not in frame.columns:
                frame[col] = pd.NA
            frame[col] = pd.to_numeric(frame[col], errors="coerce")

        if "nominal_voltage_kv" not in frame.columns:
            frame["nominal_voltage_kv"] = 132.0
        frame["nominal_voltage_kv"] = pd.to_numeric(
            frame["nominal_voltage_kv"], errors="coerce"
        ).fillna(132.0)

        frame["oil_temp_rolling_mean"] = frame["oil_temp_c"].rolling(w, min_periods=1).mean()
        frame["oil_temp_rolling_std"] = (
            frame["oil_temp_c"].rolling(w, min_periods=1).std().fillna(0.0)
        )
        frame["oil_temp_slope"] = frame["oil_temp_c"].diff().fillna(0.0)
        frame["oil_temp_delta"] = (
            frame["oil_temp_c"] - frame["oil_temp_rolling_mean"]
        ).fillna(0.0)

        frame["load_rolling_mean"] = frame["load_pct"].rolling(w, min_periods=1).mean()
        frame["load_rolling_std"] = (
            frame["load_pct"].rolling(w, min_periods=1).std().fillna(0.0)
        )
        frame["load_slope"] = frame["load_pct"].diff().fillna(0.0)
        # Soft stress: excess of rolling mean above 70% of rating.
        frame["load_stress"] = (frame["load_rolling_mean"] - 70.0).clip(lower=0.0)

        frame["voltage_rolling_mean"] = frame["voltage_kv"].rolling(w, min_periods=1).mean()
        frame["voltage_rolling_std"] = (
            frame["voltage_kv"].rolling(w, min_periods=1).std().fillna(0.0)
        )
        frame["voltage_slope"] = frame["voltage_kv"].diff().fillna(0.0)
        frame["voltage_deviation_kv"] = (
            frame["nominal_voltage_kv"] - frame["voltage_kv"]
        ).fillna(0.0)

        frame["power_factor_rolling_mean"] = (
            frame["power_factor"].rolling(w, min_periods=1).mean()
        )
        frame["power_factor_slope"] = frame["power_factor"].diff().fillna(0.0)
        frame["power_factor_deviation"] = (0.95 - frame["power_factor"]).fillna(0.0)

        frame["active_power_rolling_mean"] = (
            frame["active_power_mw"].rolling(w, min_periods=1).mean()
        )
        frame["active_power_slope"] = frame["active_power_mw"].diff().fillna(0.0)

        for col in (
            "asset_id",
            "asset_name",
            "zone",
            "criticality",
            "utility_kind",
            "time",
        ):
            if col not in frame.columns:
                frame[col] = pd.NA
        frame["utility_kind"] = frame["utility_kind"].fillna("power")

        return frame[list(FEATURE_COLUMNS)]


__all__ = [
    "FEATURE_COLUMNS",
    "MODEL_CATEGORICAL_FEATURES",
    "MODEL_NUMERIC_FEATURES",
    "TransformerFeatureBuilder",
]
