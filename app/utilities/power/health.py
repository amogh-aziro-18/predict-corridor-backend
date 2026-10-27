"""Power live condition assessment — rule-based, telemetry + short-horizon trends.

POC engineering bands for industrial corridor demo. Not plant-certified.
Thresholds are documented assumptions for Phase 14.3B operational intelligence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# --- Load (% of rating) ---
LOAD_WARN = 75.0
LOAD_HIGH = 88.0
LOAD_CRITICAL = 98.0

# --- Transformer oil temperature (°C) ---
OIL_WARN = 70.0
OIL_HIGH = 82.0
OIL_CRITICAL = 95.0

# --- Voltage (kV): absolute bands by class (from nominal magnitude) ---
# HV (~132 kV class)
HV_VOLTAGE_LOW = 125.0
HV_VOLTAGE_CRITICAL = 118.0
# MV (~33 kV class)
MV_VOLTAGE_LOW = 30.0
MV_VOLTAGE_CRITICAL = 27.0

# --- Power factor ---
POWER_FACTOR_LOW = 0.88
POWER_FACTOR_CRITICAL = 0.82

# --- Frequency (Hz) — Indian grid nominal 50 ---
FREQ_LOW = 49.5
FREQ_HIGH = 50.5

# --- Trend (per-sample slope over recent window) ---
OIL_TEMP_RISE_SLOPE = 0.12  # °C per sample in recent window
LOAD_RISE_SLOPE = 0.35  # % per sample


@dataclass(frozen=True)
class MetricEvidence:
    metric_key: str
    latest: float | None
    mean: float | None = None
    slope: float | None = None


@dataclass
class PowerConditionResult:
    """Explainable per-asset condition from Power telemetry (not ML)."""

    asset_id: str
    utility_kind: str
    zone_id: str
    asset_type: str
    condition: str  # healthy | warning | high_risk | critical | unavailable
    health_score: float
    alert_codes: list[str] = field(default_factory=list)
    drivers: list[str] = field(default_factory=list)
    evidence: list[MetricEvidence] = field(default_factory=list)
    evaluated_at: datetime | None = None
    assessment_kind: str = "rule_based_condition_assessment"


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _is_hv_voltage(voltage: float, nominal: float | None) -> bool:
    if nominal is not None and nominal >= 66.0:
        return True
    return voltage >= 50.0


def summarize_series(values: list[float]) -> tuple[float | None, float | None, float | None]:
    """Return (latest, mean, slope) for chronological values. Slope ≈ Δ per sample."""
    if not values:
        return None, None, None
    latest = float(values[-1])
    mean = sum(values) / len(values)
    if len(values) < 3:
        return latest, mean, 0.0
    # Simple linear slope: (last - first) / (n-1)
    slope = (float(values[-1]) - float(values[0])) / float(len(values) - 1)
    return latest, mean, slope


def evaluate_alerts(
    metrics: dict[str, float],
    *,
    slopes: dict[str, float] | None = None,
    asset_type: str = "",
    nominal_voltage_kv: float | None = None,
) -> list[str]:
    """
    Return Power alert codes from latest metrics + optional slopes.

    Compound codes require both level and trend evidence where noted.
    """
    slopes = slopes or {}
    alerts: list[str] = []

    load = metrics.get("load_pct")
    if load is not None:
        if load >= LOAD_CRITICAL:
            alerts.append("load_critical")
        elif load >= LOAD_HIGH:
            alerts.append("load_high")
        elif load >= LOAD_WARN:
            alerts.append("load_elevated")

    oil = metrics.get("oil_temp_c")
    if oil is not None and asset_type in {"transformer", ""}:
        if oil >= OIL_CRITICAL:
            alerts.append("oil_temp_critical")
        elif oil >= OIL_HIGH:
            alerts.append("oil_temp_high")
        elif oil >= OIL_WARN:
            alerts.append("oil_temp_elevated")

    voltage = metrics.get("voltage_kv")
    if voltage is not None:
        if _is_hv_voltage(voltage, nominal_voltage_kv):
            low, crit = HV_VOLTAGE_LOW, HV_VOLTAGE_CRITICAL
        else:
            low, crit = MV_VOLTAGE_LOW, MV_VOLTAGE_CRITICAL
        if voltage <= crit:
            alerts.append("voltage_critical_low")
        elif voltage <= low:
            alerts.append("voltage_low")

    pf = metrics.get("power_factor")
    if pf is not None:
        if pf <= POWER_FACTOR_CRITICAL:
            alerts.append("power_factor_critical")
        elif pf <= POWER_FACTOR_LOW:
            alerts.append("power_factor_low")

    freq = metrics.get("frequency_hz")
    if freq is not None and (freq < FREQ_LOW or freq > FREQ_HIGH):
        alerts.append("frequency_deviation")

    oil_slope = slopes.get("oil_temp_c")
    load_slope = slopes.get("load_pct")
    if (
        oil is not None
        and oil >= OIL_WARN
        and oil_slope is not None
        and oil_slope >= OIL_TEMP_RISE_SLOPE
    ):
        alerts.append("oil_temp_rising")
    if (
        load is not None
        and load >= LOAD_WARN
        and load_slope is not None
        and load_slope >= LOAD_RISE_SLOPE
    ):
        alerts.append("load_rising")

    # Multi-signal thermal/loading stress (transformer-focused).
    if (
        asset_type == "transformer"
        and oil is not None
        and load is not None
        and oil >= OIL_WARN
        and load >= LOAD_WARN
        and (
            (oil_slope is not None and oil_slope >= OIL_TEMP_RISE_SLOPE * 0.5)
            or oil >= OIL_HIGH
            or load >= LOAD_HIGH
        )
    ):
        alerts.append("thermal_loading_stress")

    return alerts


def condition_from_alerts(alerts: list[str], *, has_telemetry: bool) -> str:
    if not has_telemetry:
        return "unavailable"
    critical = {
        "load_critical",
        "oil_temp_critical",
        "voltage_critical_low",
        "power_factor_critical",
    }
    high = {
        "load_high",
        "oil_temp_high",
        "voltage_low",
        "thermal_loading_stress",
        "frequency_deviation",
    }
    warning = {
        "load_elevated",
        "oil_temp_elevated",
        "power_factor_low",
        "oil_temp_rising",
        "load_rising",
    }
    if any(c in critical for c in alerts):
        return "critical"
    if any(c in high for c in alerts):
        return "high_risk"
    if any(c in warning for c in alerts):
        return "warning"
    return "healthy"


def status_from_condition(condition: str, *, current: str = "operational") -> str:
    if current == "maintenance":
        return "maintenance"
    if condition == "critical":
        return "degraded"
    if condition in {"high_risk", "warning"}:
        return "degraded"
    if condition == "unavailable":
        return current
    return "operational"


def score_load(value: float | None) -> float | None:
    if value is None:
        return None
    if value <= LOAD_WARN:
        return 100.0
    if value <= LOAD_HIGH:
        return _clamp(100.0 - (value - LOAD_WARN) * 1.5)
    if value <= LOAD_CRITICAL:
        return _clamp(70.0 - (value - LOAD_HIGH) * 2.0)
    return _clamp(40.0 - (value - LOAD_CRITICAL))


def score_oil_temp(value: float | None) -> float | None:
    if value is None:
        return None
    if value <= OIL_WARN:
        return 100.0
    if value <= OIL_HIGH:
        return _clamp(100.0 - (value - OIL_WARN) * 2.0)
    if value <= OIL_CRITICAL:
        return _clamp(70.0 - (value - OIL_HIGH) * 2.5)
    return _clamp(35.0 - (value - OIL_CRITICAL))


def score_voltage(value: float | None, *, nominal: float | None = None) -> float | None:
    if value is None:
        return None
    if _is_hv_voltage(value, nominal):
        low, crit = HV_VOLTAGE_LOW, HV_VOLTAGE_CRITICAL
        ideal = 132.0
    else:
        low, crit = MV_VOLTAGE_LOW, MV_VOLTAGE_CRITICAL
        ideal = 33.0
    if value >= low:
        # Soft penalty only if far from nominal
        return _clamp(100.0 - abs(value - ideal) * 0.5)
    if value >= crit:
        return _clamp(70.0 - (low - value) * 5.0)
    return _clamp(40.0 - (crit - value) * 3.0)


def score_power_factor(value: float | None) -> float | None:
    if value is None:
        return None
    if value >= POWER_FACTOR_LOW:
        return 100.0
    if value >= POWER_FACTOR_CRITICAL:
        return _clamp(80.0 - (POWER_FACTOR_LOW - value) * 200.0)
    return _clamp(50.0 - (POWER_FACTOR_CRITICAL - value) * 150.0)


def weighted_health(
    metrics: dict[str, float],
    *,
    asset_type: str = "",
    nominal_voltage_kv: float | None = None,
    slopes: dict[str, float] | None = None,
) -> float:
    """
    Operational health score (0–100) from metric bands + mild trend penalties.

    This is a rule-based operational score, not an ML prediction or confidence.
    """
    components: list[tuple[float, float]] = []
    load_s = score_load(metrics.get("load_pct"))
    if load_s is not None:
        components.append((0.30, load_s))
    if asset_type == "transformer" or "oil_temp_c" in metrics:
        oil_s = score_oil_temp(metrics.get("oil_temp_c"))
        if oil_s is not None:
            components.append((0.30, oil_s))
    volt_s = score_voltage(metrics.get("voltage_kv"), nominal=nominal_voltage_kv)
    if volt_s is not None:
        components.append((0.25, volt_s))
    pf_s = score_power_factor(metrics.get("power_factor"))
    if pf_s is not None:
        components.append((0.15, pf_s))

    if not components:
        return 90.0

    weight_sum = sum(w for w, _ in components)
    score = sum(w * s for w, s in components) / weight_sum

    slopes = slopes or {}
    if slopes.get("oil_temp_c", 0.0) >= OIL_TEMP_RISE_SLOPE:
        score -= 5.0
    if slopes.get("load_pct", 0.0) >= LOAD_RISE_SLOPE:
        score -= 4.0
    return round(_clamp(score), 1)


def blend_health(previous: float, computed: float, *, alpha: float = 0.35) -> float:
    return round(previous * (1.0 - alpha) + computed * alpha, 1)


def drivers_from_alerts(alerts: list[str]) -> list[str]:
    labels = {
        "load_elevated": "Elevated loading",
        "load_high": "High sustained loading",
        "load_critical": "Critical loading",
        "load_rising": "Rising load trend",
        "oil_temp_elevated": "Elevated oil temperature",
        "oil_temp_high": "High oil temperature",
        "oil_temp_critical": "Critical oil temperature",
        "oil_temp_rising": "Rising oil temperature trend",
        "voltage_low": "Low voltage",
        "voltage_critical_low": "Critical low voltage",
        "power_factor_low": "Low power factor",
        "power_factor_critical": "Critical power factor",
        "frequency_deviation": "Frequency deviation",
        "thermal_loading_stress": "Combined thermal and loading stress",
    }
    return [labels.get(code, code) for code in alerts]


def is_critical_asset(*, criticality: str, alerts: list[str], health: float) -> bool:
    if criticality not in {"very-high", "high"}:
        return False
    if any(
        c in alerts
        for c in (
            "load_critical",
            "oil_temp_critical",
            "voltage_critical_low",
            "thermal_loading_stress",
        )
    ):
        return True
    return health < 70.0


def freshness_seconds(latest: datetime | None, *, now: datetime | None = None) -> float | None:
    if latest is None:
        return None
    now = now or datetime.now(timezone.utc)
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    return max(0.0, (now - latest).total_seconds())


def evaluate_asset_condition(
    *,
    asset_id: str,
    zone_id: str,
    asset_type: str,
    metrics: dict[str, float],
    series_by_metric: dict[str, list[float]] | None = None,
    nominal_voltage_kv: float | None = None,
    previous_health: float = 90.0,
) -> PowerConditionResult:
    """Full rule-based assessment with optional temporal series per metric."""
    series_by_metric = series_by_metric or {}
    slopes: dict[str, float] = {}
    evidence: list[MetricEvidence] = []
    for key, values in series_by_metric.items():
        latest, mean, slope = summarize_series(values)
        if slope is not None:
            slopes[key] = slope
        evidence.append(MetricEvidence(metric_key=key, latest=latest, mean=mean, slope=slope))
    # Ensure latest metrics appear even without history.
    for key, value in metrics.items():
        if not any(e.metric_key == key for e in evidence):
            evidence.append(MetricEvidence(metric_key=key, latest=value, mean=value, slope=0.0))

    alerts = evaluate_alerts(
        metrics,
        slopes=slopes,
        asset_type=asset_type,
        nominal_voltage_kv=nominal_voltage_kv,
    )
    has_telemetry = bool(metrics)
    condition = condition_from_alerts(alerts, has_telemetry=has_telemetry)
    computed = weighted_health(
        metrics,
        asset_type=asset_type,
        nominal_voltage_kv=nominal_voltage_kv,
        slopes=slopes,
    )
    health = blend_health(previous_health, computed) if has_telemetry else previous_health
    return PowerConditionResult(
        asset_id=asset_id,
        utility_kind="power",
        zone_id=zone_id,
        asset_type=asset_type,
        condition=condition,
        health_score=health,
        alert_codes=alerts,
        drivers=drivers_from_alerts(alerts),
        evidence=evidence,
        evaluated_at=datetime.now(timezone.utc),
    )
