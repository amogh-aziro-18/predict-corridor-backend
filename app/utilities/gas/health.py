"""Gas live condition assessment — rule-based, telemetry + short-horizon trends.

POC engineering bands for industrial corridor demo. Not plant-certified.

Threshold provenance (Phase 15.1):
- Absolute bands for compressor_load_pct / vibration / temperature / leak_indicator /
  gas_quality_pct are POC assumptions sized to the Phase 15.0 Gas simulator
  clamps and degrading drifts (see app/simulator/gas/telemetry_generator.py).
- Pressure / flow / discharge / power bands are relative to Asset.profile
  nominal_* fields from app/utilities/gas/seed_catalog.py.
- leak_indicator is a synthetic 0–1 POC signal — NOT certified CH₄ concentration.

Condition is derived ONLY from telemetry values and temporal slopes.
Zone ID / scenario mode / asset ID are NEVER used as decision inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# --- Compressor load (% of rating) — POC absolute bands ---
LOAD_WARN = 75.0
LOAD_HIGH = 88.0
LOAD_CRITICAL = 98.0

# --- Vibration (mm/s) — POC absolute bands (ISO-inspired demo only) ---
VIBE_WARN = 2.8
VIBE_HIGH = 4.5
VIBE_CRITICAL = 7.1

# --- Temperature (°C) — POC absolute bands (compressor-oriented) ---
TEMP_WARN = 65.0
TEMP_HIGH = 80.0
TEMP_CRITICAL = 95.0

# --- Leak indicator (0–1 synthetic POC) ---
LEAK_WARN = 0.25
LEAK_HIGH = 0.45
LEAK_CRITICAL = 0.70

# --- Gas quality (%) — below-band degradation ---
QUALITY_WARN = 93.0
QUALITY_HIGH = 90.0
QUALITY_CRITICAL = 85.0

# --- Relative deviation from profile nominal (fraction of nominal) ---
# Used for pressure_bar, flow_rate_nm3_h, discharge pressure, power_kw.
REL_DEV_WARN = 0.10
REL_DEV_HIGH = 0.18
REL_DEV_CRITICAL = 0.28

# --- Valve position (%) — extreme positions ---
VALVE_WARN_LOW = 20.0
VALVE_WARN_HIGH = 80.0
VALVE_HIGH_LOW = 10.0
VALVE_HIGH_HIGH = 90.0

# --- Trend (per-sample slope over recent window) ---
TEMP_RISE_SLOPE = 0.35
LOAD_RISE_SLOPE = 0.40
VIBE_RISE_SLOPE = 0.08
LEAK_RISE_SLOPE = 0.015


@dataclass(frozen=True)
class MetricEvidence:
    metric_key: str
    latest: float | None
    mean: float | None = None
    slope: float | None = None


@dataclass
class GasConditionResult:
    """Explainable per-asset condition from Gas telemetry (not ML)."""

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


def summarize_series(values: list[float]) -> tuple[float | None, float | None, float | None]:
    """Return (latest, mean, slope) for chronological values. Slope ≈ Δ per sample."""
    if not values:
        return None, None, None
    latest = float(values[-1])
    mean = sum(values) / len(values)
    if len(values) < 3:
        return latest, mean, 0.0
    slope = (float(values[-1]) - float(values[0])) / float(len(values) - 1)
    return latest, mean, slope


def _rel_deviation(value: float | None, nominal: float | None) -> float | None:
    if value is None or nominal is None or abs(float(nominal)) < 1e-9:
        return None
    return abs(float(value) - float(nominal)) / abs(float(nominal))


def _append_rel_codes(
    alerts: list[str],
    *,
    prefix: str,
    value: float | None,
    nominal: float | None,
) -> None:
    dev = _rel_deviation(value, nominal)
    if dev is None:
        return
    if dev >= REL_DEV_CRITICAL:
        alerts.append(f"{prefix}_critical")
    elif dev >= REL_DEV_HIGH:
        alerts.append(f"{prefix}_high")
    elif dev >= REL_DEV_WARN:
        alerts.append(f"{prefix}_elevated")


def evaluate_alerts(
    metrics: dict[str, float],
    *,
    slopes: dict[str, float] | None = None,
    asset_type: str = "",
    nominals: dict[str, float] | None = None,
) -> list[str]:
    """
    Return Gas alert codes from latest metrics + optional slopes.

    Only evaluates metrics present in ``metrics``. Missing metrics are ignored
    (not treated as failures). Asset-type gating skips compressor-only codes
    on regulators/feeders.
    """
    slopes = slopes or {}
    nominals = nominals or {}
    alerts: list[str] = []
    is_compressor = asset_type == "gas_compressor"
    is_regulator = asset_type == "gas_regulator_station"

    # --- Shared: pressure vs nominal ---
    _append_rel_codes(
        alerts,
        prefix="pressure",
        value=metrics.get("pressure_bar"),
        nominal=nominals.get("nominal_pressure_bar"),
    )

    # --- Shared: flow vs nominal ---
    _append_rel_codes(
        alerts,
        prefix="flow",
        value=metrics.get("flow_rate_nm3_h"),
        nominal=nominals.get("nominal_flow_nm3_h"),
    )

    # --- Shared: temperature (absolute POC bands) ---
    temp = metrics.get("temperature_c")
    if temp is not None:
        if temp >= TEMP_CRITICAL:
            alerts.append("temperature_critical")
        elif temp >= TEMP_HIGH:
            alerts.append("temperature_high")
        elif temp >= TEMP_WARN:
            alerts.append("temperature_elevated")

    # --- Shared: leak indicator (synthetic POC) ---
    leak = metrics.get("leak_indicator")
    if leak is not None:
        if leak >= LEAK_CRITICAL:
            alerts.append("leak_indicator_critical")
        elif leak >= LEAK_HIGH:
            alerts.append("leak_indicator_high")
        elif leak >= LEAK_WARN:
            alerts.append("leak_indicator_elevated")

    # --- Shared: gas quality (below-band) ---
    quality = metrics.get("gas_quality_pct")
    if quality is not None:
        if quality <= QUALITY_CRITICAL:
            alerts.append("gas_quality_critical")
        elif quality <= QUALITY_HIGH:
            alerts.append("gas_quality_high")
        elif quality <= QUALITY_WARN:
            alerts.append("gas_quality_elevated")

    # --- Compressor-only ---
    if is_compressor:
        load = metrics.get("compressor_load_pct")
        if load is not None:
            if load >= LOAD_CRITICAL:
                alerts.append("compressor_load_critical")
            elif load >= LOAD_HIGH:
                alerts.append("compressor_load_high")
            elif load >= LOAD_WARN:
                alerts.append("compressor_load_elevated")

        vibe = metrics.get("vibration_mm_s")
        if vibe is not None:
            if vibe >= VIBE_CRITICAL:
                alerts.append("vibration_critical")
            elif vibe >= VIBE_HIGH:
                alerts.append("vibration_high")
            elif vibe >= VIBE_WARN:
                alerts.append("vibration_elevated")

        _append_rel_codes(
            alerts,
            prefix="discharge_pressure",
            value=metrics.get("compressor_discharge_pressure_bar"),
            nominal=nominals.get("nominal_discharge_pressure_bar"),
        )
        _append_rel_codes(
            alerts,
            prefix="power_consumption",
            value=metrics.get("power_consumption_kw"),
            nominal=nominals.get("nominal_power_kw"),
        )

        load_slope = slopes.get("compressor_load_pct")
        vibe_slope = slopes.get("vibration_mm_s")
        temp_slope = slopes.get("temperature_c")
        if (
            load is not None
            and load >= LOAD_WARN
            and load_slope is not None
            and load_slope >= LOAD_RISE_SLOPE
        ):
            alerts.append("compressor_load_rising")
        if (
            vibe is not None
            and vibe >= VIBE_WARN
            and vibe_slope is not None
            and vibe_slope >= VIBE_RISE_SLOPE
        ):
            alerts.append("vibration_rising")
        if (
            temp is not None
            and temp >= TEMP_WARN
            and temp_slope is not None
            and temp_slope >= TEMP_RISE_SLOPE
        ):
            alerts.append("temperature_rising")

        # Compound mechanical/thermal stress (compressor only).
        if (
            load is not None
            and vibe is not None
            and load >= LOAD_WARN
            and vibe >= VIBE_WARN
            and (
                (vibe_slope is not None and vibe_slope >= VIBE_RISE_SLOPE * 0.5)
                or vibe >= VIBE_HIGH
                or load >= LOAD_HIGH
            )
        ):
            alerts.append("compressor_mechanical_stress")

    # --- Regulator-only valve position ---
    if is_regulator:
        valve = metrics.get("valve_position_pct")
        if valve is not None:
            if valve <= VALVE_HIGH_LOW or valve >= VALVE_HIGH_HIGH:
                alerts.append("valve_position_high")
            elif valve <= VALVE_WARN_LOW or valve >= VALVE_WARN_HIGH:
                alerts.append("valve_position_elevated")

    leak_slope = slopes.get("leak_indicator")
    if (
        leak is not None
        and leak >= LEAK_WARN
        and leak_slope is not None
        and leak_slope >= LEAK_RISE_SLOPE
    ):
        alerts.append("leak_indicator_rising")

    return alerts


def condition_from_alerts(alerts: list[str], *, has_telemetry: bool) -> str:
    if not has_telemetry:
        return "unavailable"
    critical = {
        "compressor_load_critical",
        "vibration_critical",
        "temperature_critical",
        "leak_indicator_critical",
        "gas_quality_critical",
        "pressure_critical",
        "flow_critical",
        "discharge_pressure_critical",
        "power_consumption_critical",
    }
    high = {
        "compressor_load_high",
        "vibration_high",
        "temperature_high",
        "leak_indicator_high",
        "gas_quality_high",
        "pressure_high",
        "flow_high",
        "discharge_pressure_high",
        "power_consumption_high",
        "compressor_mechanical_stress",
        "valve_position_high",
    }
    warning = {
        "compressor_load_elevated",
        "vibration_elevated",
        "temperature_elevated",
        "leak_indicator_elevated",
        "gas_quality_elevated",
        "pressure_elevated",
        "flow_elevated",
        "discharge_pressure_elevated",
        "power_consumption_elevated",
        "compressor_load_rising",
        "vibration_rising",
        "temperature_rising",
        "leak_indicator_rising",
        "valve_position_elevated",
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


def score_vibration(value: float | None) -> float | None:
    if value is None:
        return None
    if value <= VIBE_WARN:
        return 100.0
    if value <= VIBE_HIGH:
        return _clamp(100.0 - (value - VIBE_WARN) * 12.0)
    if value <= VIBE_CRITICAL:
        return _clamp(70.0 - (value - VIBE_HIGH) * 10.0)
    return _clamp(35.0 - (value - VIBE_CRITICAL) * 5.0)


def score_temperature(value: float | None) -> float | None:
    if value is None:
        return None
    if value <= TEMP_WARN:
        return 100.0
    if value <= TEMP_HIGH:
        return _clamp(100.0 - (value - TEMP_WARN) * 2.0)
    if value <= TEMP_CRITICAL:
        return _clamp(70.0 - (value - TEMP_HIGH) * 2.5)
    return _clamp(35.0 - (value - TEMP_CRITICAL))


def score_leak(value: float | None) -> float | None:
    if value is None:
        return None
    if value <= LEAK_WARN:
        return 100.0
    if value <= LEAK_HIGH:
        return _clamp(100.0 - (value - LEAK_WARN) * 100.0)
    if value <= LEAK_CRITICAL:
        return _clamp(70.0 - (value - LEAK_HIGH) * 80.0)
    return _clamp(30.0 - (value - LEAK_CRITICAL) * 50.0)


def score_relative(value: float | None, nominal: float | None) -> float | None:
    dev = _rel_deviation(value, nominal)
    if dev is None:
        return None
    if dev <= REL_DEV_WARN:
        return 100.0
    if dev <= REL_DEV_HIGH:
        return _clamp(100.0 - (dev - REL_DEV_WARN) * 250.0)
    if dev <= REL_DEV_CRITICAL:
        return _clamp(70.0 - (dev - REL_DEV_HIGH) * 200.0)
    return _clamp(35.0 - (dev - REL_DEV_CRITICAL) * 100.0)


def weighted_health(
    metrics: dict[str, float],
    *,
    asset_type: str = "",
    nominals: dict[str, float] | None = None,
    slopes: dict[str, float] | None = None,
) -> float:
    """
    Operational health score (0–100) from metric bands + mild trend penalties.

    Transparent rule-based operational score — not an ML prediction or confidence.
    """
    nominals = nominals or {}
    components: list[tuple[float, float]] = []

    pressure_s = score_relative(
        metrics.get("pressure_bar"), nominals.get("nominal_pressure_bar")
    )
    if pressure_s is not None:
        components.append((0.20, pressure_s))

    leak_s = score_leak(metrics.get("leak_indicator"))
    if leak_s is not None:
        components.append((0.20, leak_s))

    temp_s = score_temperature(metrics.get("temperature_c"))
    if temp_s is not None:
        components.append((0.15, temp_s))

    if asset_type == "gas_compressor":
        load_s = score_load(metrics.get("compressor_load_pct"))
        if load_s is not None:
            components.append((0.25, load_s))
        vibe_s = score_vibration(metrics.get("vibration_mm_s"))
        if vibe_s is not None:
            components.append((0.20, vibe_s))
    else:
        flow_s = score_relative(
            metrics.get("flow_rate_nm3_h"), nominals.get("nominal_flow_nm3_h")
        )
        if flow_s is not None:
            components.append((0.25, flow_s))

    if not components:
        return 90.0

    weight_sum = sum(w for w, _ in components)
    score = sum(w * s for w, s in components) / weight_sum

    slopes = slopes or {}
    if slopes.get("compressor_load_pct", 0.0) >= LOAD_RISE_SLOPE:
        score -= 4.0
    if slopes.get("vibration_mm_s", 0.0) >= VIBE_RISE_SLOPE:
        score -= 5.0
    if slopes.get("temperature_c", 0.0) >= TEMP_RISE_SLOPE:
        score -= 4.0
    if slopes.get("leak_indicator", 0.0) >= LEAK_RISE_SLOPE:
        score -= 5.0
    return round(_clamp(score), 1)


def blend_health(previous: float, computed: float, *, alpha: float = 0.35) -> float:
    return round(previous * (1.0 - alpha) + computed * alpha, 1)


def drivers_from_alerts(alerts: list[str]) -> list[str]:
    labels = {
        "compressor_load_elevated": "Elevated compressor load",
        "compressor_load_high": "High compressor load",
        "compressor_load_critical": "Critical compressor load",
        "compressor_load_rising": "Rising compressor load trend",
        "vibration_elevated": "Elevated vibration",
        "vibration_high": "High vibration",
        "vibration_critical": "Critical vibration",
        "vibration_rising": "Rising vibration trend",
        "temperature_elevated": "Elevated gas/process temperature",
        "temperature_high": "High temperature",
        "temperature_critical": "Critical temperature",
        "temperature_rising": "Rising temperature trend",
        "leak_indicator_elevated": "Elevated leak indicator (POC)",
        "leak_indicator_high": "High leak indicator (POC)",
        "leak_indicator_critical": "Critical leak indicator (POC)",
        "leak_indicator_rising": "Rising leak indicator trend (POC)",
        "gas_quality_elevated": "Degraded gas quality",
        "gas_quality_high": "Poor gas quality",
        "gas_quality_critical": "Critical gas quality",
        "pressure_elevated": "Pressure deviation from nominal",
        "pressure_high": "High pressure deviation from nominal",
        "pressure_critical": "Critical pressure deviation from nominal",
        "flow_elevated": "Flow deviation from nominal",
        "flow_high": "High flow deviation from nominal",
        "flow_critical": "Critical flow deviation from nominal",
        "discharge_pressure_elevated": "Discharge pressure deviation",
        "discharge_pressure_high": "High discharge pressure deviation",
        "discharge_pressure_critical": "Critical discharge pressure deviation",
        "power_consumption_elevated": "Power consumption deviation",
        "power_consumption_high": "High power consumption deviation",
        "power_consumption_critical": "Critical power consumption deviation",
        "valve_position_elevated": "Valve position near band edge",
        "valve_position_high": "Valve position extreme",
        "compressor_mechanical_stress": "Combined compressor load and vibration stress",
    }
    return [labels.get(code, code) for code in alerts]


def evaluate_asset_condition(
    *,
    asset_id: str,
    zone_id: str,
    asset_type: str,
    metrics: dict[str, float],
    series_by_metric: dict[str, list[float]] | None = None,
    nominals: dict[str, float] | None = None,
    previous_health: float = 90.0,
) -> GasConditionResult:
    """
    Full rule-based assessment with optional temporal series per metric.

    ``zone_id`` is recorded for provenance only — never used in threshold logic.
    """
    series_by_metric = series_by_metric or {}
    nominals = nominals or {}
    slopes: dict[str, float] = {}
    evidence: list[MetricEvidence] = []
    for key, values in series_by_metric.items():
        latest, mean, slope = summarize_series(values)
        if slope is not None:
            slopes[key] = slope
        evidence.append(MetricEvidence(metric_key=key, latest=latest, mean=mean, slope=slope))
    for key, value in metrics.items():
        if not any(e.metric_key == key for e in evidence):
            evidence.append(MetricEvidence(metric_key=key, latest=value, mean=value, slope=0.0))

    alerts = evaluate_alerts(
        metrics,
        slopes=slopes,
        asset_type=asset_type,
        nominals=nominals,
    )
    has_telemetry = bool(metrics)
    condition = condition_from_alerts(alerts, has_telemetry=has_telemetry)
    computed = weighted_health(
        metrics,
        asset_type=asset_type,
        nominals=nominals,
        slopes=slopes,
    )
    health = blend_health(previous_health, computed) if has_telemetry else previous_health
    return GasConditionResult(
        asset_id=asset_id,
        utility_kind="gas",
        zone_id=zone_id,
        asset_type=asset_type,
        condition=condition,
        health_score=health,
        alert_codes=alerts,
        drivers=drivers_from_alerts(alerts),
        evidence=evidence,
        evaluated_at=datetime.now(timezone.utc),
    )
