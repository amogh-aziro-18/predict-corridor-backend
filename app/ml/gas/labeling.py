"""
Deterministic Gas maintenance labels from Gas telemetry evidence.

Labels are derived from observable metric bands and short-horizon trends —
aligned with Phase 15.1 Gas rule-based condition thresholds.

Positive class (maintenance_label=1): cumulative operational-risk score >= 5.
Negative class (maintenance_label=0): score < 5.

POC / simulator-derived supervision: validation metrics are optimistic and
not production-grade. Labels use telemetry evidence, NOT zone/scenario names.

NOT used as label inputs:
- simulator scenario name / zone behavior enum
- zone_id identity alone
- stored profile condition string
- ML predictions

NOT used as model features (see features.py):
- maintenance_label / maintenance_label_score
- condition / health / zone (zone is context-only)
"""

from __future__ import annotations

import pandas as pd

from app.ml.gas.constants import LABEL_STRATEGY
from app.utilities.gas import health as gas_health

LABEL_COLUMN = "maintenance_label"
LABEL_SCORE_COLUMN = "maintenance_label_score"


def add_gas_labels(features: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with deterministic binary target and rule score."""
    if features is None or features.empty:
        out = features.copy() if features is not None else pd.DataFrame()
        out[LABEL_SCORE_COLUMN] = pd.Series(dtype="int64")
        out[LABEL_COLUMN] = pd.Series(dtype="int64")
        return out

    out = features.copy()
    score = pd.Series(0, index=out.index, dtype="int64")

    load = _numeric(out, "compressor_load_pct")
    load_mean = _numeric(out, "compressor_load_rolling_mean")
    load_slope = _numeric(out, "compressor_load_slope")
    vibe = _numeric(out, "vibration_mm_s")
    vibe_mean = _numeric(out, "vibration_rolling_mean")
    vibe_slope = _numeric(out, "vibration_slope")
    temp = _numeric(out, "temperature_c")
    temp_slope = _numeric(out, "temperature_slope")
    leak = _numeric(out, "leak_indicator")
    leak_slope = _numeric(out, "leak_slope")
    quality = _numeric(out, "gas_quality_pct")
    press_dev = _numeric(out, "pressure_deviation_frac")
    flow_dev = _numeric(out, "flow_deviation_frac")
    valve_dev = _numeric(out, "valve_mid_deviation")

    # Compressor load
    score += load.ge(gas_health.LOAD_CRITICAL).fillna(False).astype(int) * 4
    score += load.ge(gas_health.LOAD_HIGH).fillna(False).astype(int) * 3
    score += load.ge(gas_health.LOAD_WARN).fillna(False).astype(int) * 2
    score += load_mean.ge(gas_health.LOAD_WARN).fillna(False).astype(int)
    score += (
        load.ge(gas_health.LOAD_WARN).fillna(False)
        & load_slope.ge(gas_health.LOAD_RISE_SLOPE).fillna(False)
    ).astype(int) * 2

    # Vibration
    score += vibe.ge(gas_health.VIBE_CRITICAL).fillna(False).astype(int) * 4
    score += vibe.ge(gas_health.VIBE_HIGH).fillna(False).astype(int) * 3
    score += vibe.ge(gas_health.VIBE_WARN).fillna(False).astype(int) * 2
    score += vibe_mean.ge(gas_health.VIBE_WARN).fillna(False).astype(int)
    score += (
        vibe.ge(gas_health.VIBE_WARN).fillna(False)
        & vibe_slope.ge(gas_health.VIBE_RISE_SLOPE).fillna(False)
    ).astype(int) * 2

    # Temperature
    score += temp.ge(gas_health.TEMP_CRITICAL).fillna(False).astype(int) * 4
    score += temp.ge(gas_health.TEMP_HIGH).fillna(False).astype(int) * 3
    score += temp.ge(gas_health.TEMP_WARN).fillna(False).astype(int) * 2
    score += (
        temp.ge(gas_health.TEMP_WARN).fillna(False)
        & temp_slope.ge(gas_health.TEMP_RISE_SLOPE).fillna(False)
    ).astype(int) * 2

    # Leak indicator (synthetic POC)
    score += leak.ge(gas_health.LEAK_CRITICAL).fillna(False).astype(int) * 4
    score += leak.ge(gas_health.LEAK_HIGH).fillna(False).astype(int) * 3
    score += leak.ge(gas_health.LEAK_WARN).fillna(False).astype(int) * 2
    score += (
        leak.ge(gas_health.LEAK_WARN).fillna(False)
        & leak_slope.ge(gas_health.LEAK_RISE_SLOPE).fillna(False)
    ).astype(int) * 2

    # Gas quality (below-band)
    score += quality.le(gas_health.QUALITY_CRITICAL).fillna(False).astype(int) * 4
    score += quality.le(gas_health.QUALITY_HIGH).fillna(False).astype(int) * 3
    score += quality.le(gas_health.QUALITY_WARN).fillna(False).astype(int) * 2

    # Relative pressure / flow deviation
    score += press_dev.ge(gas_health.REL_DEV_CRITICAL).fillna(False).astype(int) * 3
    score += press_dev.ge(gas_health.REL_DEV_HIGH).fillna(False).astype(int) * 2
    score += press_dev.ge(gas_health.REL_DEV_WARN).fillna(False).astype(int)
    score += flow_dev.ge(gas_health.REL_DEV_CRITICAL).fillna(False).astype(int) * 3
    score += flow_dev.ge(gas_health.REL_DEV_HIGH).fillna(False).astype(int) * 2
    score += flow_dev.ge(gas_health.REL_DEV_WARN).fillna(False).astype(int)

    # Valve extreme (regulator) — distance from mid ≥ 40 ≈ position ≤10 or ≥90
    score += valve_dev.ge(40.0).fillna(False).astype(int) * 2
    score += valve_dev.ge(30.0).fillna(False).astype(int)

    # Combined mechanical stress spirit
    score += (
        load.ge(gas_health.LOAD_WARN).fillna(False)
        & vibe.ge(gas_health.VIBE_WARN).fillna(False)
    ).astype(int) * 2

    out[LABEL_SCORE_COLUMN] = score
    out[LABEL_COLUMN] = score.ge(5).astype(int)
    return out


def label_distribution(features: pd.DataFrame) -> dict[str, int]:
    if LABEL_COLUMN not in features.columns:
        return {"normal": 0, "maintenance_risk": 0}
    counts = features[LABEL_COLUMN].value_counts().to_dict()
    return {
        "normal": int(counts.get(0, 0)),
        "maintenance_risk": int(counts.get(1, 0)),
    }


def label_strategy_name() -> str:
    return LABEL_STRATEGY


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(float("nan"), index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


__all__ = [
    "LABEL_COLUMN",
    "LABEL_SCORE_COLUMN",
    "add_gas_labels",
    "label_distribution",
    "label_strategy_name",
]
