"""Constants for Gas predictive-maintenance ML (Phase 15.3)."""

from __future__ import annotations

# All Gas asset types share one corridor model (not zone-specific).
# Type-specific metrics are optional (NaN → training median at matrix build).
GAS_ASSET_TYPES: tuple[str, ...] = (
    "gas_compressor",
    "gas_regulator_station",
    "gas_feeder",
)

# Telemetry metrics used for Gas PdM panels (union across asset types).
GAS_METRIC_KEYS: tuple[str, ...] = (
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
)

DEFAULT_RESAMPLE_FREQ = "10s"
DEFAULT_ROLLING_WINDOW = 12
DEFAULT_RESAMPLE_ENABLED = False

MODEL_VERSION_GAS_XGBOOST = "gas_xgboost_v1"

MIN_TRAINING_ROWS = 40
MIN_POSITIVE_ROWS = 8
MIN_NEGATIVE_ROWS = 8

# Deterministic operational evidence from Gas telemetry bands (POC supervision).
LABEL_STRATEGY = "gas_operational_risk_v1"

# Per-asset newest telemetry row budget (avoids global LIMIT starvation).
INFERENCE_HISTORY_ROWS_PER_ASSET = 12_000
