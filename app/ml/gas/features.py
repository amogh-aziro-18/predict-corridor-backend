"""Gas feature engineering for predictive-maintenance models."""

from __future__ import annotations

import pandas as pd

from app.ml.gas.constants import DEFAULT_ROLLING_WINDOW, GAS_METRIC_KEYS

# Explicit feature contract for gas_xgboost_v1.
# Excludes: zone, scenario, condition, health, status, alerts, labels (leakage).
FEATURE_COLUMNS: tuple[str, ...] = (
    "time",
    "asset_id",
    "asset_name",
    "utility_kind",
    "zone",  # context only — not a model input
    "criticality",
    "asset_type",
    "nominal_pressure_bar",
    "nominal_flow_nm3_h",
    "nominal_discharge_pressure_bar",
    "nominal_power_kw",
    "pressure_bar",
    "flow_rate_nm3_h",
    "temperature_c",
    "leak_indicator",
    "gas_quality_pct",
    "compressor_load_pct",
    "vibration_mm_s",
    "compressor_discharge_pressure_bar",
    "power_consumption_kw",
    "valve_position_pct",
    "pressure_rolling_mean",
    "pressure_rolling_std",
    "pressure_slope",
    "pressure_deviation_frac",
    "flow_rolling_mean",
    "flow_slope",
    "flow_deviation_frac",
    "temperature_rolling_mean",
    "temperature_slope",
    "leak_rolling_mean",
    "leak_slope",
    "gas_quality_rolling_mean",
    "gas_quality_slope",
    "compressor_load_rolling_mean",
    "compressor_load_rolling_std",
    "compressor_load_slope",
    "load_stress",
    "vibration_rolling_mean",
    "vibration_rolling_std",
    "vibration_slope",
    "discharge_rolling_mean",
    "discharge_slope",
    "discharge_deviation_frac",
    "power_kw_rolling_mean",
    "power_kw_slope",
    "power_kw_deviation_frac",
    "valve_mid_deviation",
)

MODEL_NUMERIC_FEATURES: tuple[str, ...] = (
    "pressure_bar",
    "flow_rate_nm3_h",
    "temperature_c",
    "leak_indicator",
    "gas_quality_pct",
    "compressor_load_pct",
    "vibration_mm_s",
    "compressor_discharge_pressure_bar",
    "power_consumption_kw",
    "valve_position_pct",
    "pressure_rolling_mean",
    "pressure_rolling_std",
    "pressure_slope",
    "pressure_deviation_frac",
    "flow_rolling_mean",
    "flow_slope",
    "flow_deviation_frac",
    "temperature_rolling_mean",
    "temperature_slope",
    "leak_rolling_mean",
    "leak_slope",
    "gas_quality_rolling_mean",
    "gas_quality_slope",
    "compressor_load_rolling_mean",
    "compressor_load_rolling_std",
    "compressor_load_slope",
    "load_stress",
    "vibration_rolling_mean",
    "vibration_rolling_std",
    "vibration_slope",
    "discharge_rolling_mean",
    "discharge_slope",
    "discharge_deviation_frac",
    "power_kw_rolling_mean",
    "power_kw_slope",
    "power_kw_deviation_frac",
    "valve_mid_deviation",
    "nominal_pressure_bar",
    "nominal_flow_nm3_h",
    "nominal_discharge_pressure_bar",
    "nominal_power_kw",
)

# Asset criticality + type — NOT zone (avoids learning zone==C/D shortcuts).
MODEL_CATEGORICAL_FEATURES: tuple[str, ...] = ("criticality", "asset_type")

FORBIDDEN_MODEL_FEATURES: frozenset[str] = frozenset(
    {
        "zone",
        "zone_id",
        "scenario",
        "scenario_name",
        "condition",
        "health",
        "status",
        "alert_severity",
        "recommendation",
        "maintenance_label",
        "maintenance_label_score",
        "condition_alert_codes",
        "condition_drivers",
    }
)


class GasFeatureBuilder:
    """Derive PdM features from a Gas dataset panel (chronological per asset)."""

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

        for col in GAS_METRIC_KEYS:
            if col not in frame.columns:
                frame[col] = pd.NA
            frame[col] = pd.to_numeric(frame[col], errors="coerce")

        for nom, default in (
            ("nominal_pressure_bar", 28.0),
            ("nominal_flow_nm3_h", 2000.0),
            ("nominal_discharge_pressure_bar", 32.0),
            ("nominal_power_kw", 380.0),
        ):
            if nom not in frame.columns:
                frame[nom] = default
            frame[nom] = pd.to_numeric(frame[nom], errors="coerce").fillna(default)

        frame["pressure_rolling_mean"] = frame["pressure_bar"].rolling(w, min_periods=1).mean()
        frame["pressure_rolling_std"] = (
            frame["pressure_bar"].rolling(w, min_periods=1).std().fillna(0.0)
        )
        frame["pressure_slope"] = frame["pressure_bar"].diff().fillna(0.0)
        frame["pressure_deviation_frac"] = (
            (frame["pressure_bar"] - frame["nominal_pressure_bar"]).abs()
            / frame["nominal_pressure_bar"].clip(lower=1e-6)
        )

        frame["flow_rolling_mean"] = frame["flow_rate_nm3_h"].rolling(w, min_periods=1).mean()
        frame["flow_slope"] = frame["flow_rate_nm3_h"].diff().fillna(0.0)
        frame["flow_deviation_frac"] = (
            (frame["flow_rate_nm3_h"] - frame["nominal_flow_nm3_h"]).abs()
            / frame["nominal_flow_nm3_h"].clip(lower=1e-6)
        )

        frame["temperature_rolling_mean"] = (
            frame["temperature_c"].rolling(w, min_periods=1).mean()
        )
        frame["temperature_slope"] = frame["temperature_c"].diff().fillna(0.0)

        frame["leak_rolling_mean"] = frame["leak_indicator"].rolling(w, min_periods=1).mean()
        frame["leak_slope"] = frame["leak_indicator"].diff().fillna(0.0)

        frame["gas_quality_rolling_mean"] = (
            frame["gas_quality_pct"].rolling(w, min_periods=1).mean()
        )
        frame["gas_quality_slope"] = frame["gas_quality_pct"].diff().fillna(0.0)

        frame["compressor_load_rolling_mean"] = (
            frame["compressor_load_pct"].rolling(w, min_periods=1).mean()
        )
        frame["compressor_load_rolling_std"] = (
            frame["compressor_load_pct"].rolling(w, min_periods=1).std().fillna(0.0)
        )
        frame["compressor_load_slope"] = frame["compressor_load_pct"].diff().fillna(0.0)
        frame["load_stress"] = (frame["compressor_load_rolling_mean"] - 70.0).clip(lower=0.0)

        frame["vibration_rolling_mean"] = (
            frame["vibration_mm_s"].rolling(w, min_periods=1).mean()
        )
        frame["vibration_rolling_std"] = (
            frame["vibration_mm_s"].rolling(w, min_periods=1).std().fillna(0.0)
        )
        frame["vibration_slope"] = frame["vibration_mm_s"].diff().fillna(0.0)

        frame["discharge_rolling_mean"] = (
            frame["compressor_discharge_pressure_bar"].rolling(w, min_periods=1).mean()
        )
        frame["discharge_slope"] = (
            frame["compressor_discharge_pressure_bar"].diff().fillna(0.0)
        )
        frame["discharge_deviation_frac"] = (
            (
                frame["compressor_discharge_pressure_bar"]
                - frame["nominal_discharge_pressure_bar"]
            ).abs()
            / frame["nominal_discharge_pressure_bar"].clip(lower=1e-6)
        )

        frame["power_kw_rolling_mean"] = (
            frame["power_consumption_kw"].rolling(w, min_periods=1).mean()
        )
        frame["power_kw_slope"] = frame["power_consumption_kw"].diff().fillna(0.0)
        frame["power_kw_deviation_frac"] = (
            (frame["power_consumption_kw"] - frame["nominal_power_kw"]).abs()
            / frame["nominal_power_kw"].clip(lower=1e-6)
        )

        frame["valve_mid_deviation"] = (frame["valve_position_pct"] - 50.0).abs()

        for col in (
            "asset_id",
            "asset_name",
            "zone",
            "criticality",
            "asset_type",
            "utility_kind",
            "time",
        ):
            if col not in frame.columns:
                frame[col] = pd.NA
        frame["utility_kind"] = frame["utility_kind"].fillna("gas")

        return frame[list(FEATURE_COLUMNS)]


__all__ = [
    "FEATURE_COLUMNS",
    "FORBIDDEN_MODEL_FEATURES",
    "MODEL_CATEGORICAL_FEATURES",
    "MODEL_NUMERIC_FEATURES",
    "GasFeatureBuilder",
]
