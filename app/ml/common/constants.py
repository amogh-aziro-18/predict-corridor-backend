"""Shared constants for ML dataset construction from live telemetry."""

from __future__ import annotations

# Metrics used when building corridor demand / ops forecast frames.
FORECAST_METRIC_KEYS: tuple[str, ...] = (
    "flow_rate",
    "pressure",
    "reservoir_level",
)

# Metrics used when building pump predictive-maintenance frames.
MAINTENANCE_METRIC_KEYS: tuple[str, ...] = (
    "flow_rate",
    "pressure",
    "pump_speed",
    "pump_running",
)

# Preferred asset types for demand signal (first with data wins).
DEMAND_ASSET_TYPE_PRIORITY: tuple[str, ...] = (
    "flow_meter",
    "distribution_network",
    "pump_station",
)

DEMAND_METRIC_KEY = "flow_rate"
DEMAND_AGGREGATION = "sum"  # corridor demand = sum of preferred assets' flow

DEFAULT_RESAMPLE_FREQ = "5min"
DEFAULT_ROLLING_WINDOW = 12  # windows of resampled points (~1h at 5min)

MODEL_VERSION_PROPHET = "prophet_water_v1"
# v3: telemetry-only model inputs (no health/status/zone/scenario leakage).
MODEL_VERSION_XGBOOST = "pump_xgboost_v3"

# Per-pump telemetry row budget when loading all stations for PdM.
# Mirrors Power's per-transformer newest-window policy.
INFERENCE_HISTORY_ROWS_PER_ASSET = 12_000
