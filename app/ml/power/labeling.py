"""
Deterministic transformer maintenance labels from Power telemetry evidence.

Labels are derived from observable metric bands and short-horizon trends —
the same engineering thresholds used by the rule-based Power condition engine.

Positive class (maintenance_label=1): cumulative operational-risk score >= 5.
Negative class (maintenance_label=0): score < 5.

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

from app.ml.power.constants import LABEL_STRATEGY
from app.utilities.power import health as power_health

LABEL_COLUMN = "maintenance_label"
LABEL_SCORE_COLUMN = "maintenance_label_score"


def add_transformer_labels(features: pd.DataFrame) -> pd.DataFrame:
    """
    Return a copy with deterministic binary target and rule score.

    Score components (aligned with Power POC thresholds):
    - oil temperature elevated / high / critical
    - load elevated / high / critical
    - rising oil / load trends while elevated
    - voltage sag (HV bands)
    - low / critical power factor
    """
    if features is None or features.empty:
        out = features.copy() if features is not None else pd.DataFrame()
        out[LABEL_SCORE_COLUMN] = pd.Series(dtype="int64")
        out[LABEL_COLUMN] = pd.Series(dtype="int64")
        return out

    out = features.copy()
    score = pd.Series(0, index=out.index, dtype="int64")

    oil = _numeric(out, "oil_temp_c")
    oil_mean = _numeric(out, "oil_temp_rolling_mean")
    oil_slope = _numeric(out, "oil_temp_slope")
    load = _numeric(out, "load_pct")
    load_mean = _numeric(out, "load_rolling_mean")
    load_slope = _numeric(out, "load_slope")
    voltage = _numeric(out, "voltage_kv")
    pf = _numeric(out, "power_factor")

    # Thermal evidence
    score += oil.ge(power_health.OIL_CRITICAL).fillna(False).astype(int) * 4
    score += oil.ge(power_health.OIL_HIGH).fillna(False).astype(int) * 3
    score += oil.ge(power_health.OIL_WARN).fillna(False).astype(int) * 2
    score += oil_mean.ge(power_health.OIL_WARN).fillna(False).astype(int)
    score += (
        oil.ge(power_health.OIL_WARN).fillna(False)
        & oil_slope.ge(power_health.OIL_TEMP_RISE_SLOPE).fillna(False)
    ).astype(int) * 2

    # Loading evidence
    score += load.ge(power_health.LOAD_CRITICAL).fillna(False).astype(int) * 4
    score += load.ge(power_health.LOAD_HIGH).fillna(False).astype(int) * 3
    score += load.ge(power_health.LOAD_WARN).fillna(False).astype(int) * 2
    score += load_mean.ge(power_health.LOAD_WARN).fillna(False).astype(int)
    score += (
        load.ge(power_health.LOAD_WARN).fillna(False)
        & load_slope.ge(power_health.LOAD_RISE_SLOPE).fillna(False)
    ).astype(int) * 2

    # Voltage sag (HV transformer class)
    score += voltage.le(power_health.HV_VOLTAGE_CRITICAL).fillna(False).astype(int) * 3
    score += voltage.le(power_health.HV_VOLTAGE_LOW).fillna(False).astype(int) * 2

    # Power factor
    score += pf.le(power_health.POWER_FACTOR_CRITICAL).fillna(False).astype(int) * 3
    score += pf.le(power_health.POWER_FACTOR_LOW).fillna(False).astype(int) * 2

    # Combined thermal + loading (mirrors thermal_loading_stress spirit)
    score += (
        oil.ge(power_health.OIL_WARN).fillna(False)
        & load.ge(power_health.LOAD_WARN).fillna(False)
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
    "add_transformer_labels",
    "label_distribution",
    "label_strategy_name",
]
