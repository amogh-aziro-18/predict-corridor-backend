"""Communications live condition assessment — rule-based, telemetry + short-horizon trends.

POC ASSUMPTION — NOT ENGINEERING STANDARD.

Threshold provenance (Phase 17.4):
- Absolute bands for packet_loss_pct / cabinet_temp_c are POC assumptions sized to
  the Phase 17.3 Communications simulator clamps and degrading drifts
  (see app/simulator/communications/telemetry_generator.py).
- RTT bands are positive overage vs Asset.profile nominal_link_rtt_ms from
  app/utilities/communications/seed_catalog.py.
- Power draw is secondary/contextual relative deviation vs nominal_power_draw_w;
  there is no power_draw_critical code.
- Cabinet temperature bands are NOT Gas process-temp bands and are NOT telecom/
  NEBS engineering standards.

Condition is derived ONLY from telemetry values and temporal slopes.
Zone ID / scenario mode / asset ID are NEVER used as decision inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# --- Link availability (float discrete) ---
LINK_DOWN = 0.5  # link_up < 0.5 ⇒ unavailable (dominates all other severities)

# --- RTT overage vs nominal_link_rtt_ms (ms) — POC ASSUMPTION ---
RTT_DELTA_WARN = 8.0
RTT_DELTA_HIGH = 20.0
RTT_DELTA_CRITICAL = 40.0
RTT_RISE_SLOPE = 1.0  # ms per sample

# --- Packet loss (%) — POC ASSUMPTION ---
LOSS_WARN = 1.0
LOSS_HIGH = 5.0
LOSS_CRITICAL = 15.0
LOSS_RISE_SLOPE = 0.15  # % per sample

# --- Cabinet temperature (°C) — POC ASSUMPTION (not Gas / not NEBS) ---
TEMP_WARN = 40.0
TEMP_HIGH = 50.0
TEMP_CRITICAL = 60.0
TEMP_RISE_SLOPE = 0.30  # °C per sample

# --- Power draw relative deviation (fraction of nominal) — secondary only ---
POWER_REL_WARN = 0.10
POWER_REL_HIGH = 0.18


@dataclass(frozen=True)
class MetricEvidence:
    metric_key: str
    latest: float | None
    mean: float | None = None
    slope: float | None = None


@dataclass
class CommunicationsConditionResult:
    """Explainable per-asset condition from Communications telemetry (not ML)."""

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


def _rtt_delta(value: float | None, nominal: float | None) -> float | None:
    """Positive overage only (latest − nominal)."""
    if value is None or nominal is None:
        return None
    return max(0.0, float(value) - float(nominal))


def evaluate_alerts(
    metrics: dict[str, float],
    *,
    slopes: dict[str, float] | None = None,
    asset_type: str = "",
    nominals: dict[str, float] | None = None,
) -> list[str]:
    """
    Return Communications finding codes from latest metrics + optional slopes.

    ``asset_type`` is accepted for API parity with Gas/Power; both Comm asset
    types share the same five metrics and absolute loss/temp bands.
    Missing metrics are ignored (not treated as failures), except that callers
    use empty metrics to mean unavailable via ``condition_from_alerts``.
    """
    del asset_type  # shared bands; nominals differentiate types
    slopes = slopes or {}
    nominals = nominals or {}
    alerts: list[str] = []

    link_up = metrics.get("link_up")
    link_down = link_up is not None and float(link_up) < LINK_DOWN

    # Availability finding (condition_from_alerts maps this to unavailable).
    if link_down:
        alerts.append("link_down")

    # --- RTT vs nominal (positive overage) ---
    rtt = metrics.get("link_rtt_ms")
    rtt_delta = _rtt_delta(rtt, nominals.get("nominal_link_rtt_ms"))
    if rtt_delta is not None:
        if rtt_delta >= RTT_DELTA_CRITICAL:
            alerts.append("rtt_critical")
        elif rtt_delta >= RTT_DELTA_HIGH:
            alerts.append("rtt_high")
        elif rtt_delta >= RTT_DELTA_WARN:
            alerts.append("rtt_elevated")

    # --- Packet loss (absolute POC bands) ---
    loss = metrics.get("packet_loss_pct")
    if loss is not None:
        if loss >= LOSS_CRITICAL:
            alerts.append("packet_loss_critical")
        elif loss >= LOSS_HIGH:
            alerts.append("packet_loss_high")
        elif loss >= LOSS_WARN:
            alerts.append("packet_loss_elevated")

    # --- Cabinet temperature (absolute POC — not Gas / not NEBS) ---
    temp = metrics.get("cabinet_temp_c")
    if temp is not None:
        if temp >= TEMP_CRITICAL:
            alerts.append("cabinet_temp_critical")
        elif temp >= TEMP_HIGH:
            alerts.append("cabinet_temp_high")
        elif temp >= TEMP_WARN:
            alerts.append("cabinet_temp_elevated")

    # --- Power draw (secondary; skipped while link is down) ---
    if not link_down:
        power = metrics.get("power_draw_w")
        power_dev = _rel_deviation(power, nominals.get("nominal_power_draw_w"))
        if power_dev is not None:
            if power_dev >= POWER_REL_HIGH:
                alerts.append("power_draw_high")
            elif power_dev >= POWER_REL_WARN:
                alerts.append("power_draw_elevated")
            # Intentionally no power_draw_critical.

    # --- Rising codes (level + slope) ---
    rtt_slope = slopes.get("link_rtt_ms")
    if (
        rtt_delta is not None
        and rtt_delta >= RTT_DELTA_WARN
        and rtt_slope is not None
        and rtt_slope >= RTT_RISE_SLOPE
    ):
        alerts.append("rtt_rising")

    loss_slope = slopes.get("packet_loss_pct")
    if (
        loss is not None
        and loss >= LOSS_WARN
        and loss_slope is not None
        and loss_slope >= LOSS_RISE_SLOPE
    ):
        alerts.append("packet_loss_rising")

    temp_slope = slopes.get("cabinet_temp_c")
    if (
        temp is not None
        and temp >= TEMP_WARN
        and temp_slope is not None
        and temp_slope >= TEMP_RISE_SLOPE
    ):
        alerts.append("cabinet_temp_rising")

    # --- Compound path quality (Gas-style) ---
    rtt_elevated_plus = any(c in alerts for c in ("rtt_elevated", "rtt_high", "rtt_critical"))
    loss_elevated_plus = any(
        c in alerts for c in ("packet_loss_elevated", "packet_loss_high", "packet_loss_critical")
    )
    rtt_high_plus = any(c in alerts for c in ("rtt_high", "rtt_critical"))
    loss_high_plus = any(c in alerts for c in ("packet_loss_high", "packet_loss_critical"))
    rising_path = "rtt_rising" in alerts or "packet_loss_rising" in alerts
    if rtt_elevated_plus and loss_elevated_plus and (rising_path or rtt_high_plus or loss_high_plus):
        alerts.append("path_quality_stress")

    return alerts


def condition_from_alerts(alerts: list[str], *, has_telemetry: bool, link_up: float | None) -> str:
    """
    Deterministic precedence:

    1. no telemetry → unavailable
    2. link_up < 0.5 → unavailable
    3. any critical → critical
    4. any high → high_risk
    5. any warning/rising → warning
    6. healthy
    """
    if not has_telemetry:
        return "unavailable"
    if link_up is not None and float(link_up) < LINK_DOWN:
        return "unavailable"

    critical = {
        "rtt_critical",
        "packet_loss_critical",
        "cabinet_temp_critical",
    }
    high = {
        "rtt_high",
        "packet_loss_high",
        "cabinet_temp_high",
        "power_draw_high",
        "path_quality_stress",
    }
    warning = {
        "rtt_elevated",
        "packet_loss_elevated",
        "cabinet_temp_elevated",
        "power_draw_elevated",
        "rtt_rising",
        "packet_loss_rising",
        "cabinet_temp_rising",
    }
    if any(c in critical for c in alerts):
        return "critical"
    if any(c in high for c in alerts):
        return "high_risk"
    if any(c in warning for c in alerts):
        return "warning"
    return "healthy"


def status_from_condition(condition: str, *, current: str = "operational") -> str:
    """Repository-compatible status mapping (Gas/Power parity)."""
    if current == "maintenance":
        return "maintenance"
    if condition == "critical":
        return "degraded"
    if condition in {"high_risk", "warning"}:
        return "degraded"
    if condition == "unavailable":
        # Authoritative condition lives in profile.condition — do not invent status.
        return current
    return "operational"


def score_rtt_delta(delta: float | None) -> float | None:
    if delta is None:
        return None
    if delta < RTT_DELTA_WARN:
        return 100.0
    if delta < RTT_DELTA_HIGH:
        return _clamp(100.0 - (delta - RTT_DELTA_WARN) * 2.0)
    if delta < RTT_DELTA_CRITICAL:
        return _clamp(70.0 - (delta - RTT_DELTA_HIGH) * 1.5)
    return _clamp(40.0 - (delta - RTT_DELTA_CRITICAL) * 0.5)


def score_packet_loss(value: float | None) -> float | None:
    if value is None:
        return None
    if value < LOSS_WARN:
        return 100.0
    if value < LOSS_HIGH:
        return _clamp(100.0 - (value - LOSS_WARN) * 8.0)
    if value < LOSS_CRITICAL:
        return _clamp(70.0 - (value - LOSS_HIGH) * 3.0)
    return _clamp(35.0 - (value - LOSS_CRITICAL) * 1.5)


def score_cabinet_temp(value: float | None) -> float | None:
    if value is None:
        return None
    if value < TEMP_WARN:
        return 100.0
    if value < TEMP_HIGH:
        return _clamp(100.0 - (value - TEMP_WARN) * 2.5)
    if value < TEMP_CRITICAL:
        return _clamp(70.0 - (value - TEMP_HIGH) * 2.5)
    return _clamp(35.0 - (value - TEMP_CRITICAL) * 1.5)


def score_power_relative(value: float | None, nominal: float | None) -> float | None:
    dev = _rel_deviation(value, nominal)
    if dev is None:
        return None
    if dev < POWER_REL_WARN:
        return 100.0
    if dev < POWER_REL_HIGH:
        return _clamp(100.0 - (dev - POWER_REL_WARN) * 250.0)
    return _clamp(70.0 - (dev - POWER_REL_HIGH) * 200.0)


def score_link_up(value: float | None) -> float | None:
    if value is None:
        return None
    return 100.0 if float(value) >= LINK_DOWN else 10.0


def weighted_health(
    metrics: dict[str, float],
    *,
    nominals: dict[str, float] | None = None,
    slopes: dict[str, float] | None = None,
) -> float:
    """
    Operational health score (0–100) from metric bands + mild trend penalties.

    Transparent rule-based operational score — not an ML prediction or confidence.
    Power draw weight is intentionally low (~0.10); secondary/contextual only.
    """
    nominals = nominals or {}
    components: list[tuple[float, float]] = []

    link_s = score_link_up(metrics.get("link_up"))
    if link_s is not None:
        components.append((0.30, link_s))

    rtt_delta = _rtt_delta(metrics.get("link_rtt_ms"), nominals.get("nominal_link_rtt_ms"))
    rtt_s = score_rtt_delta(rtt_delta)
    if rtt_s is not None:
        components.append((0.25, rtt_s))

    loss_s = score_packet_loss(metrics.get("packet_loss_pct"))
    if loss_s is not None:
        components.append((0.25, loss_s))

    temp_s = score_cabinet_temp(metrics.get("cabinet_temp_c"))
    if temp_s is not None:
        components.append((0.10, temp_s))

    link_up = metrics.get("link_up")
    link_down = link_up is not None and float(link_up) < LINK_DOWN
    if not link_down:
        power_s = score_power_relative(
            metrics.get("power_draw_w"), nominals.get("nominal_power_draw_w")
        )
        if power_s is not None:
            components.append((0.10, power_s))

    if not components:
        return 90.0

    weight_sum = sum(w for w, _ in components)
    score = sum(w * s for w, s in components) / weight_sum

    slopes = slopes or {}
    if slopes.get("link_rtt_ms", 0.0) >= RTT_RISE_SLOPE:
        score -= 4.0
    if slopes.get("packet_loss_pct", 0.0) >= LOSS_RISE_SLOPE:
        score -= 4.0
    if slopes.get("cabinet_temp_c", 0.0) >= TEMP_RISE_SLOPE:
        score -= 3.0
    return round(_clamp(score), 1)


def blend_health(previous: float, computed: float, *, alpha: float = 0.35) -> float:
    return round(previous * (1.0 - alpha) + computed * alpha, 1)


def drivers_from_alerts(alerts: list[str]) -> list[str]:
    labels = {
        "link_down": "Link path down (unavailable)",
        "rtt_elevated": "Elevated link RTT vs nominal",
        "rtt_high": "High link RTT vs nominal",
        "rtt_critical": "Critical link RTT vs nominal",
        "rtt_rising": "Rising link RTT trend",
        "packet_loss_elevated": "Elevated packet loss",
        "packet_loss_high": "High packet loss",
        "packet_loss_critical": "Critical packet loss",
        "packet_loss_rising": "Rising packet loss trend",
        "cabinet_temp_elevated": "Elevated cabinet temperature (POC)",
        "cabinet_temp_high": "High cabinet temperature (POC)",
        "cabinet_temp_critical": "Critical cabinet temperature (POC)",
        "cabinet_temp_rising": "Rising cabinet temperature trend (POC)",
        "power_draw_elevated": "Power draw deviation from nominal",
        "power_draw_high": "High power draw deviation from nominal",
        "path_quality_stress": "Combined RTT and packet-loss path stress",
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
) -> CommunicationsConditionResult:
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
    link_up = metrics.get("link_up")
    condition = condition_from_alerts(alerts, has_telemetry=has_telemetry, link_up=link_up)
    computed = weighted_health(metrics, nominals=nominals, slopes=slopes)
    health = blend_health(previous_health, computed) if has_telemetry else previous_health
    return CommunicationsConditionResult(
        asset_id=asset_id,
        utility_kind="communications",
        zone_id=zone_id,
        asset_type=asset_type,
        condition=condition,
        health_score=health,
        alert_codes=alerts,
        drivers=drivers_from_alerts(alerts),
        evidence=evidence,
        evaluated_at=datetime.now(timezone.utc),
    )
