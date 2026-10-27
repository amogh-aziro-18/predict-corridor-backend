"""
Weather → Alert producer (Phase 03).

Persists threshold breaches from WeatherImpactAssessment as Alert rows
(source=weather). Uses AlertService.sync_condition_set for idempotent
upsert + clearance. Does not create work orders or mutate telemetry.

POC assumption: utility_kind is stored as ``cross_utility`` because weather
signals are corridor/zone centroid assessments, not a single utility feed.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.api.schemas.weather_impact import (
    WeatherImpactAssessmentOut,
    WeatherImpactObservationKind,
    WeatherImpactSignalOut,
    WeatherImpactSignalType,
)
from app.domain.enums import AlertScope, AlertSource
from app.services.alert_service import AlertService
from app.services.weather_impact_thresholds import SEVERITY_RANK

# Fingerprint utility_kind for weather-origin alerts (not water/power/gas/communications).
WEATHER_ALERT_UTILITY_KIND = "cross_utility"

_ALERT_SEVERITIES = frozenset({"critical", "high", "medium", "low"})

_SIGNAL_TITLES: dict[str, str] = {
    WeatherImpactSignalType.PRECIPITATION.value: "Precipitation threshold",
    WeatherImpactSignalType.TEMPERATURE.value: "Temperature threshold",
    WeatherImpactSignalType.RELATIVE_HUMIDITY.value: "Humidity threshold",
    WeatherImpactSignalType.WIND_SPEED.value: "Wind speed threshold",
    WeatherImpactSignalType.WIND_GUST.value: "Wind gust threshold",
    WeatherImpactSignalType.PRESSURE.value: "Pressure threshold",
    WeatherImpactSignalType.WEATHER_CODE.value: "Adverse weather code",
}


def weather_alert_code(
    *,
    signal_type: str,
    observation_kind: str,
) -> str:
    """Deterministic alert_code for fingerprinting (one active alert per type×kind×scope)."""
    return f"weather_{signal_type}_{observation_kind}"


def known_weather_alert_codes() -> frozenset[str]:
    codes: set[str] = set()
    for st in WeatherImpactSignalType:
        for kind in WeatherImpactObservationKind:
            codes.add(weather_alert_code(signal_type=st.value, observation_kind=kind.value))
    return frozenset(codes)


def _normalize_alert_severity(severity: str) -> str:
    s = (severity or "").strip().lower()
    if s == "info":
        return "low"
    if s in _ALERT_SEVERITIES:
        return s
    return "medium"


def weather_alert_meta(code: str) -> tuple[str, str | None, str]:
    """Fallback meta when code is known but signal payload is unavailable."""
    kind = "current"
    signal_type = code.removeprefix("weather_")
    if code.endswith("_current"):
        kind = "current"
        signal_type = code[len("weather_") : -len("_current")]
    elif code.endswith("_forecast"):
        kind = "forecast"
        signal_type = code[len("weather_") : -len("_forecast")]
    title = _SIGNAL_TITLES.get(signal_type, signal_type.replace("_", " ").title())
    return f"{title} ({kind})", f"Weather impact code: {code}", "medium"


def _collapse_signals(signals: list[WeatherImpactSignalOut]) -> dict[str, WeatherImpactSignalOut]:
    """One signal per alert_code — keep highest severity."""
    best: dict[str, WeatherImpactSignalOut] = {}
    for signal in signals:
        code = weather_alert_code(
            signal_type=signal.signal_type.value,
            observation_kind=signal.observation_kind.value,
        )
        prev = best.get(code)
        if prev is None:
            best[code] = signal
            continue
        if SEVERITY_RANK.get(signal.severity, 0) > SEVERITY_RANK.get(prev.severity, 0):
            best[code] = signal
    return best


def sync_weather_alerts_from_assessment(
    db: Session,
    assessment: WeatherImpactAssessmentOut,
) -> None:
    """
    Set-diff sync weather alerts for the assessment scope.

    - Upserts present threshold codes (source=weather).
    - Resolves active weather alerts for this scope whose codes cleared.
    - Commits so GET /alerts sees durable rows across requests.
    """
    scope = (
        AlertScope.ZONE.value
        if assessment.scope.value == "zone"
        else AlertScope.CORRIDOR.value
    )
    zone_id = assessment.zone_id

    collapsed = _collapse_signals(assessment.signals)
    present_codes = set(collapsed.keys())

    def meta_for_code(code: str) -> tuple[str, str | None, str]:
        signal = collapsed.get(code)
        if signal is None:
            return weather_alert_meta(code)
        title_base = _SIGNAL_TITLES.get(
            signal.signal_type.value,
            signal.signal_type.value.replace("_", " ").title(),
        )
        kind = signal.observation_kind.value
        title = f"{title_base} ({kind})"
        description = (
            f"{signal.explanation} "
            f"Measured {signal.measured_value} {signal.unit}. "
            f"Rule: {signal.threshold_rule}. "
            f"Provider: {signal.source}. "
            f"Does not mutate telemetry or create work orders."
        )
        return title, description, _normalize_alert_severity(signal.severity)

    AlertService(db).sync_condition_set(
        source=AlertSource.WEATHER.value,
        utility_kind=WEATHER_ALERT_UTILITY_KIND,
        scope=scope,
        zone_id=zone_id,
        asset_id=None,
        present_codes=present_codes,
        meta_for_code=meta_for_code,
        resolve_notes="Weather threshold condition cleared",
    )
    db.commit()
