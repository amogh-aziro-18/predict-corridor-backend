"""Constants for Gas corridor demand forecasting (Prophet) — Phase 15.4."""

from __future__ import annotations

from datetime import timedelta

# Volumetric demand proxy — written for CMP/REG/GFDR; summable Nm³/h.
# Prefer one network layer (feeders) so CMP+REG+GFDR are not triple-counted.
GAS_DEMAND_METRIC_KEY = "flow_rate_nm3_h"
GAS_DEMAND_AGGREGATION = "sum"
GAS_DEMAND_UNIT = "Nm³/h"

# Water-style priority: delivery offtake → regulation → transmission.
GAS_DEMAND_ASSET_TYPE_PRIORITY: tuple[str, ...] = (
    "gas_feeder",
    "gas_regulator_station",
    "gas_compressor",
)

MODEL_NAME = "gas_demand_forecast"
MODEL_VERSION_GAS_PROPHET = "gas_demand_forecast_v1"

# Dense Gas simulator ticks → prefer 1-minute bins when span allows;
# otherwise keep second-level cadence (no fabricated gaps).
PREFERRED_RESAMPLE_FREQ = "1min"
FALLBACK_RESAMPLE_FREQ = "1s"

MIN_TRAINING_POINTS = 10
DEFAULT_FORECAST_HOURS = 24

MIN_SPAN_FOR_HOUR_HORIZON = timedelta(hours=2)
SHORT_HISTORY_FORECAST_PERIODS = 24

HOLDOUT_FRACTION = 0.20
MIN_HOLDOUT_POINTS = 5
