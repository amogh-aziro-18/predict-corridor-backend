"""
Deterministic Environment Impact Assessment (Phase 9C).

Consumes EnvironmentService / EnvironmentSignal observations only —
never calls the AQI provider directly. Produces impact signals when
explicit thresholds are breached.

Limitations:
- Corridor/zone centroid scope only — no asset-level impact.
- US AQI classified only when metadata.aqi_standard == "us_aqi".
- Pollutant thresholds are POC operational bands, not CPCB limits.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session

from app.api.schemas.environment_impact import (
    EnvironmentImpactAssessmentOut,
    EnvironmentImpactEvidenceOut,
    EnvironmentImpactSignalOut,
    EnvironmentImpactSignalType,
)
from app.api.schemas.environment_signal import (
    EnvironmentObservationKind,
    EnvironmentObservationSet,
    EnvironmentSignal,
    EnvironmentSignalScope,
)
from app.services.environment_impact_thresholds import (
    AQI_POLICY,
    SEVERITY_RANK,
    THRESHOLD_BASIS,
    ThresholdBreaches,
    classify_co,
    classify_no2,
    classify_o3,
    classify_pm10,
    classify_pm25,
    classify_so2,
    classify_us_aqi,
    max_severity,
)
from app.services.environment_service import EnvironmentService

_SUPPORTED = {
    EnvironmentImpactSignalType.AQI.value,
    EnvironmentImpactSignalType.PM25.value,
    EnvironmentImpactSignalType.PM10.value,
    EnvironmentImpactSignalType.NO2.value,
    EnvironmentImpactSignalType.SO2.value,
    EnvironmentImpactSignalType.CO.value,
    EnvironmentImpactSignalType.O3.value,
}

_POLLUTANT_CLASSIFIERS: dict[str, Callable[[float], ThresholdBreaches | None]] = {
    "pm25": classify_pm25,
    "pm10": classify_pm10,
    "no2": classify_no2,
    "so2": classify_so2,
    "co": classify_co,
    "o3": classify_o3,
}


class EnvironmentImpactService:
    """Rule-based environmental impact assessment over EnvironmentService data."""

    def __init__(
        self,
        db: Session,
        *,
        environment_service: EnvironmentService | None = None,
    ) -> None:
        self._db = db
        self._env = environment_service or EnvironmentService(db)

    def assess(
        self,
        *,
        zone_id: str | None = None,
        utility_kind: str | None = None,
        hours: int = 24,
    ) -> EnvironmentImpactAssessmentOut:
        """
        Assess environmental impact for corridor or zone scope.

        Propagates EnvironmentUnavailableError from EnvironmentService when
        observations cannot be loaded (no fabrication).
        """
        hours = max(0, min(int(hours), 168))
        observations = self._env.get_observation_set(
            zone_id=zone_id,
            utility_kind=utility_kind,
            hours=hours,
        )
        return self.assess_observation_set(observations, forecast_hours=hours)

    def assess_observation_set(
        self,
        observation_set: EnvironmentObservationSet,
        *,
        forecast_hours: int,
    ) -> EnvironmentImpactAssessmentOut:
        """
        Assess a pre-fetched EnvironmentObservationSet (testable without network).
        """
        scope = observation_set.scope
        zone_id = observation_set.zone_id

        current_signals = [
            s
            for s in observation_set.signals
            if s.observation_kind == EnvironmentObservationKind.CURRENT
            and s.signal_type in _SUPPORTED
        ]
        forecast_signals = [
            s
            for s in observation_set.signals
            if s.observation_kind == EnvironmentObservationKind.FORECAST
            and s.signal_type in _SUPPORTED
        ]

        impact_signals: list[EnvironmentImpactSignalOut] = []
        impact_signals.extend(self._assess_current(current_signals, scope=scope, zone_id=zone_id))
        impact_signals.extend(
            self._assess_forecast_peaks(forecast_signals, scope=scope, zone_id=zone_id)
        )

        kinds: list[EnvironmentObservationKind] = []
        if current_signals:
            kinds.append(EnvironmentObservationKind.CURRENT)
        if forecast_signals:
            kinds.append(EnvironmentObservationKind.FORECAST)

        overall = max_severity([s.severity for s in impact_signals])
        note = observation_set.note
        if any(s.observation_kind == EnvironmentObservationKind.FORECAST for s in impact_signals):
            forward = (
                "Includes forward-looking forecast impact (worst breach per pollutant "
                "across the forecast horizon)."
            )
            note = f"{note} {forward}".strip() if note else forward

        return EnvironmentImpactAssessmentOut(
            location=observation_set.location,
            provider_meta=observation_set.provider_meta,
            assessed_at=datetime.now(timezone.utc),
            scope=scope,
            zone_id=zone_id,
            utility_kind=observation_set.utility_kind,
            forecast_hours=forecast_hours,
            observation_kinds=kinds,
            overall_severity=overall,
            signals=impact_signals,
            evidence=EnvironmentImpactEvidenceOut(
                note=(
                    "Deterministic threshold assessment over EnvironmentSignal observations. "
                    "Centroid corridor/zone scope only — no asset-level impact."
                ),
                threshold_basis=THRESHOLD_BASIS,
                aqi_policy=AQI_POLICY,
            ),
            note=note,
        )

    def _assess_current(
        self,
        signals: list[EnvironmentSignal],
        *,
        scope: EnvironmentSignalScope,
        zone_id: str | None,
    ) -> list[EnvironmentImpactSignalOut]:
        out: list[EnvironmentImpactSignalOut] = []
        for signal in signals:
            breach = self._classify(signal)
            if breach is None:
                continue
            out.append(self._to_impact(signal, breach=breach, scope=scope, zone_id=zone_id))
        return out

    def _assess_forecast_peaks(
        self,
        signals: list[EnvironmentSignal],
        *,
        scope: EnvironmentSignalScope,
        zone_id: str | None,
    ) -> list[EnvironmentImpactSignalOut]:
        """
        Deterministic forecast rule: for each signal_type, evaluate all forecast
        points and emit a single impact signal for the worst severity breach
        (ties → highest measured_value, then latest observed_at).
        """
        by_type: dict[str, list[EnvironmentSignal]] = {}
        for signal in signals:
            by_type.setdefault(signal.signal_type, []).append(signal)

        out: list[EnvironmentImpactSignalOut] = []
        for _signal_type, group in by_type.items():
            candidates: list[tuple[EnvironmentSignal, ThresholdBreaches]] = []
            for signal in group:
                breach = self._classify(signal)
                if breach is None:
                    continue
                candidates.append((signal, breach))
            if not candidates:
                continue
            worst_signal, worst_breach = max(
                candidates,
                key=lambda pair: (
                    SEVERITY_RANK.get(pair[1].severity, 0),
                    pair[0].measured_value,
                    pair[0].observed_at,
                ),
            )
            impact = self._to_impact(
                worst_signal,
                breach=worst_breach,
                scope=scope,
                zone_id=zone_id,
                explanation_suffix=" (worst forecast breach in horizon).",
            )
            out.append(impact)
        return out

    def _classify(self, signal: EnvironmentSignal) -> ThresholdBreaches | None:
        signal_type = signal.signal_type
        if signal_type == "aqi":
            standard = str(signal.metadata.get("aqi_standard") or "").strip().lower()
            if standard != "us_aqi":
                # Unknown / non-US AQI — do not silently apply US thresholds.
                return None
            return classify_us_aqi(signal.measured_value)

        classifier = _POLLUTANT_CLASSIFIERS.get(signal_type)
        if classifier is None:
            return None
        return classifier(signal.measured_value)

    @staticmethod
    def _to_impact(
        signal: EnvironmentSignal,
        *,
        breach: ThresholdBreaches,
        scope: EnvironmentSignalScope,
        zone_id: str | None,
        explanation_suffix: str = "",
    ) -> EnvironmentImpactSignalOut:
        metadata = dict(signal.metadata)
        if signal.signal_type == "aqi":
            metadata["aqi_standard"] = "us_aqi"
            metadata.setdefault("aqi_index", "United States Air Quality Index")
        return EnvironmentImpactSignalOut(
            signal_type=signal.signal_type,
            severity=breach.severity,
            scope=scope,
            zone_id=zone_id,
            observation_kind=signal.observation_kind,
            measured_value=signal.measured_value,
            unit=signal.unit,
            threshold_rule=breach.threshold_rule,
            explanation=f"{breach.explanation}{explanation_suffix}",
            observed_at=signal.observed_at,
            source=signal.source,
            confidence=signal.confidence,
            metadata=metadata,
        )
