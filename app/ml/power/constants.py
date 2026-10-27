"""Constants for Power transformer predictive-maintenance ML."""

from __future__ import annotations

TRANSFORMER_ASSET_TYPE = "transformer"

# Telemetry metrics used for transformer PdM frames (from Power seed/sim).
TRANSFORMER_METRIC_KEYS: tuple[str, ...] = (
    "oil_temp_c",
    "load_pct",
    "voltage_kv",
    "active_power_mw",
    "power_factor",
)

DEFAULT_RESAMPLE_FREQ = "10s"
DEFAULT_ROLLING_WINDOW = 12  # windows of ordered samples (ticks), not wall-clock hours

MODEL_VERSION_TRANSFORMER_XGBOOST = "transformer_xgboost_v1"

# Minimum labelled rows and positives required before training is allowed.
MIN_TRAINING_ROWS = 40
MIN_POSITIVE_ROWS = 8
MIN_NEGATIVE_ROWS = 8

# Label strategy version — deterministic operational evidence, not scenario names.
LABEL_STRATEGY = "power_transformer_operational_risk_v1"

# Power simulator ticks are dense (seconds). Default: do not 5-minute downsample
# or temporal history collapses into one bin per asset.
DEFAULT_RESAMPLE_ENABLED = False

# Per-transformer telemetry row budget for training/inference history loads.
# Keeps each XFMR's newest window even when corridor history is large.
INFERENCE_HISTORY_ROWS_PER_ASSET = 12_000
