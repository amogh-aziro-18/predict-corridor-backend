"""Water live-health scoring and operational thresholds (telemetry-driven)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


# Operational thresholds — alerts / critical only when crossed.
PRESSURE_LOW = 1.8
PRESSURE_HIGH = 5.5
PRESSURE_CRITICAL_LOW = 1.2
PRESSURE_IDEAL = (2.5, 4.0)

LEVEL_LOW = 30.0
LEVEL_CRITICAL = 20.0
LEVEL_IDEAL = (55.0, 90.0)

FLOW_LOW = 50.0
FLOW_STABILITY_SPAN = 200.0  # deviation from corridor median band

PUMP_STOPPED = 0.5  # pump_running < 0.5 ⇒ unavailable


@dataclass(frozen=True)
class TelemetrySnapshot:
    metrics: dict[str, float]
    recorded_at: datetime | None = None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def score_pressure(value: float | None) -> float | None:
    if value is None:
        return None
    lo, hi = PRESSURE_IDEAL
    if lo <= value <= hi:
        return 100.0
    if value < lo:
        # 0 bar → ~40, ideal_low → 100
        return _clamp(40.0 + 60.0 * (value / lo) if lo else 40.0)
    # above ideal: soft penalty until HIGH, then steeper
    if value <= PRESSURE_HIGH:
        return _clamp(100.0 - 25.0 * ((value - hi) / max(PRESSURE_HIGH - hi, 0.01)))
    return _clamp(50.0 - 20.0 * (value - PRESSURE_HIGH))


def score_level(value: float | None) -> float | None:
    if value is None:
        return None
    lo, hi = LEVEL_IDEAL
    if lo <= value <= hi:
        return 100.0
    if value < lo:
        return _clamp(100.0 * (value / lo))
    # overfill soft penalty
    return _clamp(100.0 - (value - hi) * 1.5)


def score_flow_stability(value: float | None, *, reference: float | None = None) -> float | None:
    if value is None:
        return None
    if value < FLOW_LOW:
        return _clamp(30.0 + value)
    ref = reference if reference and reference > 0 else value
    deviation = abs(value - ref)
    # Within span → high score; beyond → gradual drop
    return _clamp(100.0 - (deviation / FLOW_STABILITY_SPAN) * 50.0)


def score_pump_availability(pump_running: float | None, pump_speed: float | None) -> float | None:
    if pump_running is None and pump_speed is None:
        return None
    if pump_running is not None:
        return 100.0 if pump_running >= PUMP_STOPPED else 15.0
    if pump_speed is not None:
        return 100.0 if pump_speed > 100 else 15.0
    return None


def weighted_health(
    *,
    pressure: float | None = None,
    level: float | None = None,
    flow: float | None = None,
    flow_reference: float | None = None,
    pump_running: float | None = None,
    pump_speed: float | None = None,
) -> float:
    """
    Weighted live health:
      Pressure 40% · Tank/Reservoir level 30% · Flow stability 20% · Pump availability 10%
    Missing components redistribute weight proportionally.
    """
    components: list[tuple[float, float]] = []
    p = score_pressure(pressure)
    if p is not None:
        components.append((0.40, p))
    lvl = score_level(level)
    if lvl is not None:
        components.append((0.30, lvl))
    f = score_flow_stability(flow, reference=flow_reference)
    if f is not None:
        components.append((0.20, f))
    pump = score_pump_availability(pump_running, pump_speed)
    if pump is not None:
        components.append((0.10, pump))

    if not components:
        return 90.0

    weight_sum = sum(w for w, _ in components)
    score = sum(w * s for w, s in components) / weight_sum
    return round(_clamp(score), 1)


def evaluate_alerts(metrics: dict[str, float]) -> list[str]:
    """Return alert codes when operational thresholds are exceeded."""
    alerts: list[str] = []
    pressure = metrics.get("pressure")
    if pressure is not None:
        if pressure <= PRESSURE_CRITICAL_LOW:
            alerts.append("pressure_critical_low")
        elif pressure < PRESSURE_LOW:
            alerts.append("pressure_low")
        elif pressure > PRESSURE_HIGH:
            alerts.append("pressure_high")

    for key in ("tank_level", "reservoir_level", "current_level"):
        level = metrics.get(key)
        if level is None:
            continue
        if level <= LEVEL_CRITICAL:
            alerts.append("level_critical")
        elif level < LEVEL_LOW:
            alerts.append("level_low")
        break

    flow = metrics.get("flow_rate")
    if flow is not None and flow < FLOW_LOW:
        alerts.append("flow_low")

    pump_running = metrics.get("pump_running")
    pump_speed = metrics.get("pump_speed")
    if (pump_running is not None and pump_running < PUMP_STOPPED) or (
        pump_speed is not None and pump_speed <= 100 and "pump_speed" in metrics
    ):
        # Only flag pump assets that publish pump metrics.
        if "pump_running" in metrics or "pump_speed" in metrics:
            if pump_running is not None and pump_running < PUMP_STOPPED:
                alerts.append("pump_stopped")
            elif pump_speed is not None and pump_speed <= 100:
                alerts.append("pump_stopped")

    turbidity = metrics.get("turbidity")
    if turbidity is not None and turbidity > 4.0:
        alerts.append("turbidity_high")

    return alerts


def status_from_alerts(alerts: list[str], *, current: str = "operational") -> str:
    if current == "maintenance":
        return "maintenance"
    if any(a.endswith("critical") or a == "pressure_critical_low" for a in alerts):
        return "offline" if "pressure_critical_low" in alerts and "level_critical" in alerts else "degraded"
    if alerts:
        return "degraded"
    return "operational"


def is_critical_asset(
    *,
    criticality: str,
    alerts: list[str],
    health: float,
) -> bool:
    if criticality not in {"very-high", "high"}:
        return False
    if alerts:
        return True
    return health < 75.0


def blend_health(previous: float, computed: float, *, alpha: float = 0.35) -> float:
    """Exponential smooth so health moves gradually across ticks."""
    return round(previous * (1.0 - alpha) + computed * alpha, 1)


def metrics_from_rows(rows: list[Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in rows:
        key = getattr(row, "metric_key", None) or row.get("metric_key")
        value = getattr(row, "value", None) if not isinstance(row, dict) else row.get("value")
        if key is not None and isinstance(value, (int, float)):
            out[str(key)] = float(value)
    return out


def freshness_seconds(latest: datetime | None, *, now: datetime | None = None) -> float | None:
    if latest is None:
        return None
    now = now or datetime.now(timezone.utc)
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    return max(0.0, (now - latest).total_seconds())
