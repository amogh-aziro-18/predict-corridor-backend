"""
Deterministic demo labels derived from persisted operational state.

No labels are fabricated or loaded from external files. Each feature row is
labelled from existing telemetry-derived features and PostgreSQL asset state:

* critical/low pressure and repeated low-pressure windows
* stopped pump / very low speed
* low flow
* degraded, offline, or maintenance asset status on the latest asset sample
* low telemetry-derived asset health on the latest asset sample

The binary target is ``maintenance_label = 1`` when the cumulative rule score
is at least 5. A single warning condition is therefore insufficient: the label
requires a critical condition or multiple corroborating signals. This provides
a reproducible Sprint 6C supervision strategy until real failure/work-order
outcomes become available.
"""

from __future__ import annotations

import pandas as pd


LABEL_COLUMN = "maintenance_label"
LABEL_SCORE_COLUMN = "maintenance_label_score"


def add_maintenance_labels(features: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with deterministic binary target and rule score."""
    if features is None or features.empty:
        out = features.copy() if features is not None else pd.DataFrame()
        out[LABEL_SCORE_COLUMN] = pd.Series(dtype="int64")
        out[LABEL_COLUMN] = pd.Series(dtype="int64")
        return out

    out = features.copy()
    score = pd.Series(0, index=out.index, dtype="int64")

    pressure = _numeric(out, "pressure")
    pressure_mean = _numeric(out, "pressure_rolling_mean")
    flow = _numeric(out, "flow_rate")
    flow_mean = _numeric(out, "flow_rolling_mean")
    speed = _numeric(out, "pump_speed")
    running = _numeric(out, "pump_running")
    health = _numeric(out, "health")
    status = out.get("status", pd.Series("", index=out.index)).astype(str).str.lower()

    # Repeated/critical hydraulic conditions. Thresholds distinguish a
    # critical excursion from a recoverable low-pressure warning.
    score += pressure.le(1.0).fillna(False).astype(int) * 3
    score += pressure_mean.lt(1.4).fillna(False).astype(int) * 2
    score += flow.lt(40.0).fillna(False).astype(int) * 2
    score += flow_mean.lt(75.0).fillna(False).astype(int)

    # Pump availability conditions.
    score += running.lt(0.5).fillna(False).astype(int) * 3
    score += speed.le(100.0).fillna(False).astype(int) * 2

    # Asset.health/status are current persisted state, not historical snapshots.
    # Apply them only to each asset's latest sample to avoid leaking today's
    # state backward across the complete telemetry history.
    if "asset_id" in out.columns and "time" in out.columns:
        times = pd.to_datetime(out["time"], utc=True, errors="coerce")
        latest_time = times.groupby(out["asset_id"]).transform("max")
        latest_sample = times.eq(latest_time)
    else:
        latest_sample = pd.Series(False, index=out.index)
    score += (
        health.lt(65.0).fillna(False).astype(int)
        * latest_sample.astype(int)
        * 2
    )
    score += (
        health.between(65.0, 75.0, inclusive="left").fillna(False).astype(int)
        * latest_sample.astype(int)
    )
    score += (
        status.isin({"offline", "maintenance"}).astype(int)
        * latest_sample.astype(int)
        * 2
    )
    score += status.eq("degraded").astype(int) * latest_sample.astype(int)

    out[LABEL_SCORE_COLUMN] = score
    out[LABEL_COLUMN] = score.ge(5).astype(int)
    return out


def label_distribution(features: pd.DataFrame) -> dict[str, int]:
    """Readable target counts for training metadata."""
    if LABEL_COLUMN not in features.columns:
        return {"normal": 0, "maintenance_risk": 0}
    counts = features[LABEL_COLUMN].value_counts().to_dict()
    return {
        "normal": int(counts.get(0, 0)),
        "maintenance_risk": int(counts.get(1, 0)),
    }


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(float("nan"), index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


__all__ = [
    "LABEL_COLUMN",
    "LABEL_SCORE_COLUMN",
    "add_maintenance_labels",
    "label_distribution",
]
