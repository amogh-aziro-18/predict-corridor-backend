"""Constants for Power corridor load forecasting (Prophet)."""

from __future__ import annotations

from datetime import timedelta

# Target metric — corridor load = sum of active_power_mw across Power assets.
POWER_LOAD_METRIC_KEY = "active_power_mw"
POWER_LOAD_AGGREGATION = "sum"  # corridor total MW
POWER_LOAD_UNIT = "MW"

MODEL_NAME = "power_load_forecast"
MODEL_VERSION_POWER_PROPHET = "power_load_forecast_v1"

# Dense Power simulator ticks → prefer 1-minute bins when span allows;
# otherwise fall back to observed second-level cadence (no fabricated gaps).
PREFERRED_RESAMPLE_FREQ = "1min"
FALLBACK_RESAMPLE_FREQ = "1s"

MIN_TRAINING_POINTS = 10
DEFAULT_FORECAST_HOURS = 24

# If observed history span is shorter than this, hour-scale horizons are
# not meaningfully supported — use a short period-based horizon instead.
MIN_SPAN_FOR_HOUR_HORIZON = timedelta(hours=2)
SHORT_HISTORY_FORECAST_PERIODS = 24

# Chronological holdout fraction for backtest (when enough points exist).
HOLDOUT_FRACTION = 0.20
MIN_HOLDOUT_POINTS = 5
