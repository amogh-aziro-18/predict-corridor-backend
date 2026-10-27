"""Deterministic operational intelligence — rule-based recommendations (no LLM).

Weather Impact signals (Phase 8D) are consumed from WeatherImpactService and
translated into recommendation drafts via ``weather_impact_rules``. Weather
does not prove asset failure; forecast and current conditions remain distinct.

Environment Impact signals (Phase 9D) are consumed from EnvironmentImpactService
via ``environment_impact_rules``. AQI remains US AQI when labeled ``us_aqi``.
Resilience posture (Phase 10A) is composed via ResilienceAssessmentService and
translated with ``resilience_rules`` (at most one recommendation per scope).
Cross-utility dependency impact (Phase 16.2) is consumed from
CrossUtilityImpactService via ``cross_utility_dependency_rules`` (one draft per
critical/unavailable source with dependents). Human approval is required before
work order creation.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha1
from typing import Any

from sqlalchemy.orm import Session

from app.api.schemas.environment_impact import (
    EnvironmentImpactAssessmentOut,
    EnvironmentImpactSignalOut,
)
from app.api.schemas.environment_signal import EnvironmentObservationKind
from app.api.schemas.resilience import ResilienceAssessmentOut
from app.api.schemas.weather_impact import (
    WeatherImpactAssessmentOut,
    WeatherImpactObservationKind,
    WeatherImpactSignalOut,
)
from app.core.errors import EnvironmentUnavailableError, WeatherUnavailableError
from app.domain.repositories import AssetRepository, TelemetryRepository
from app.domain.repositories.zone_repository import ZoneRepository
from app.scenarios.definitions import ScenarioDefinition, ScenarioKey, get_scenario
from app.scenarios.resolution import resolve_effective_scenario
from app.services.environment_impact_service import EnvironmentImpactService
from app.services.resilience_assessment_service import ResilienceAssessmentService
from app.services.weather_impact_service import WeatherImpactService
from app.utilities.communications import health as communications_health
from app.utilities.gas import health as gas_health
from app.utilities.water import health as water_health
from app.utilities.power import health as power_health


@dataclass(frozen=True)
class RecommendationDraft:
    rule_code: str
    priority: str
    severity: str
    title: str
    description: str
    affected_asset: str
    affected_zone: str
    recommendation: str
    estimated_business_impact: str
    confidence: float
    # Optional structured context (Phase 16.4 cross-utility snapshot). Other rules omit.
    context: dict[str, Any] | None = None


class OperationalIntelligenceService:
    """
    Analyses live telemetry and active scenario to produce engineering
    recommendations using threshold and scenario-aware rules.
    """

    HIGH_PRIORITIES = frozenset({"critical", "high"})

    def __init__(self, db: Session) -> None:
        self._db = db
        self._assets = AssetRepository(db)
        self._telemetry = TelemetryRepository(db)

    def generate(
        self,
        *,
        scenario_key: str,
        scenario_intensity: float,
        zone_scenarios: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> list[RecommendationDraft]:
        now = now or datetime.now(timezone.utc)
        try:
            default_scenario = get_scenario(scenario_key)
        except KeyError:
            default_scenario = get_scenario(ScenarioKey.NORMAL.value)

        assets = self._assets.list(utility_kind="water")
        latest = self._telemetry.latest_rows(utility_kind="water")

        metrics_by_asset: dict[str, dict[str, float]] = defaultdict(dict)
        for asset_id, _name, tel in latest:
            metrics_by_asset[asset_id][tel.metric_key] = float(tel.value)

        corridor_flows = [
            m["flow_rate"] for m in metrics_by_asset.values() if "flow_rate" in m
        ]
        flow_reference = (
            sum(corridor_flows) / len(corridor_flows) if corridor_flows else None
        )

        drafts: list[RecommendationDraft] = []
        for asset in assets:
            metrics = metrics_by_asset.get(asset.id, {})
            alerts = water_health.evaluate_alerts(metrics)
            level = (
                metrics.get("tank_level")
                or metrics.get("reservoir_level")
                or (
                    float(asset.profile["current_level"])
                    if isinstance(asset.profile, dict)
                    and isinstance(asset.profile.get("current_level"), (int, float))
                    else None
                )
            )
            health = water_health.weighted_health(
                pressure=metrics.get("pressure"),
                level=level,
                flow=metrics.get("flow_rate"),
                flow_reference=flow_reference,
                pump_running=metrics.get("pump_running"),
                pump_speed=metrics.get("pump_speed"),
            )

            drafts.extend(
                self._telemetry_rules(
                    asset_id=asset.id,
                    asset_name=asset.name,
                    zone_id=asset.zone_id,
                    asset_type=asset.type,
                    metrics=metrics,
                    alerts=alerts,
                    health=health,
                )
            )

            zone_key, zone_intensity = resolve_effective_scenario(
                global_key=scenario_key,
                global_intensity=scenario_intensity,
                zone_scenarios=zone_scenarios,
                zone_id=asset.zone_id,
            )
            try:
                scenario = get_scenario(zone_key.value)
            except KeyError:
                scenario = default_scenario

            drafts.extend(
                self._scenario_rules(
                    scenario=scenario,
                    intensity=zone_intensity,
                    asset_id=asset.id,
                    asset_name=asset.name,
                    zone_id=asset.zone_id,
                    asset_type=asset.type,
                    metrics=metrics,
                    health=health,
                )
            )

        # Phase 14.3B — Power rule-based condition assessments (not ML).
        power_assets = self._assets.list(utility_kind="power")
        power_latest = self._telemetry.latest_rows(utility_kind="power")
        power_metrics_by_asset: dict[str, dict[str, float]] = defaultdict(dict)
        for asset_id, _name, tel in power_latest:
            power_metrics_by_asset[asset_id][tel.metric_key] = float(tel.value)

        for asset in power_assets:
            metrics = power_metrics_by_asset.get(asset.id, {})
            if not metrics:
                continue
            profile = dict(asset.profile or {}) if isinstance(asset.profile, dict) else {}
            nominal = profile.get("nominal_voltage_kv")
            nominal_f = float(nominal) if isinstance(nominal, (int, float)) else None
            # Prefer alert codes already computed during telemetry sync when present.
            stored_codes = profile.get("condition_alert_codes")
            if isinstance(stored_codes, list) and stored_codes:
                alerts = [str(c) for c in stored_codes]
            else:
                alerts = power_health.evaluate_alerts(
                    metrics,
                    asset_type=asset.type,
                    nominal_voltage_kv=nominal_f,
                )
            health = float(asset.health)
            drafts.extend(
                self._power_telemetry_rules(
                    asset_id=asset.id,
                    asset_name=asset.name,
                    zone_id=asset.zone_id,
                    asset_type=asset.type,
                    metrics=metrics,
                    alerts=alerts,
                    health=health,
                    condition=str(profile.get("condition") or ""),
                )
            )

        # Phase 15.2 — Gas rule-based condition assessments (not ML).
        gas_assets = self._assets.list(utility_kind="gas")
        gas_latest = self._telemetry.latest_rows(utility_kind="gas")
        gas_metrics_by_asset: dict[str, dict[str, float]] = defaultdict(dict)
        for asset_id, _name, tel in gas_latest:
            gas_metrics_by_asset[asset_id][tel.metric_key] = float(tel.value)

        for asset in gas_assets:
            metrics = gas_metrics_by_asset.get(asset.id, {})
            if not metrics:
                continue
            profile = dict(asset.profile or {}) if isinstance(asset.profile, dict) else {}
            stored_codes = profile.get("condition_alert_codes")
            if isinstance(stored_codes, list) and stored_codes:
                alerts = [str(c) for c in stored_codes]
            else:
                nominals: dict[str, float] = {}
                for key in (
                    "nominal_pressure_bar",
                    "nominal_flow_nm3_h",
                    "nominal_discharge_pressure_bar",
                    "nominal_power_kw",
                ):
                    raw = profile.get(key)
                    if isinstance(raw, (int, float)):
                        nominals[key] = float(raw)
                alerts = gas_health.evaluate_alerts(
                    metrics,
                    asset_type=asset.type,
                    nominals=nominals,
                )
            drafts.extend(
                self._gas_telemetry_rules(
                    asset_id=asset.id,
                    asset_name=asset.name,
                    zone_id=asset.zone_id,
                    asset_type=asset.type,
                    metrics=metrics,
                    alerts=alerts,
                    health=float(asset.health),
                    condition=str(profile.get("condition") or ""),
                )
            )

        # Phase 17.5 — Communications rule-based condition assessments (not ML).
        comm_assets = self._assets.list(utility_kind="communications")
        comm_latest = self._telemetry.latest_rows(utility_kind="communications")
        comm_metrics_by_asset: dict[str, dict[str, float]] = defaultdict(dict)
        for asset_id, _name, tel in comm_latest:
            comm_metrics_by_asset[asset_id][tel.metric_key] = float(tel.value)

        for asset in comm_assets:
            metrics = comm_metrics_by_asset.get(asset.id, {})
            if not metrics:
                continue
            profile = dict(asset.profile or {}) if isinstance(asset.profile, dict) else {}
            stored_codes = profile.get("condition_alert_codes")
            if isinstance(stored_codes, list) and stored_codes:
                alerts = [str(c) for c in stored_codes]
            else:
                nominals: dict[str, float] = {}
                for key in (
                    "nominal_link_rtt_ms",
                    "nominal_packet_loss_pct",
                    "nominal_cabinet_temp_c",
                    "nominal_power_draw_w",
                ):
                    raw = profile.get(key)
                    if isinstance(raw, (int, float)):
                        nominals[key] = float(raw)
                alerts = communications_health.evaluate_alerts(
                    metrics,
                    asset_type=asset.type,
                    nominals=nominals,
                )
            drafts.extend(
                self._communications_telemetry_rules(
                    asset_id=asset.id,
                    asset_name=asset.name,
                    zone_id=asset.zone_id,
                    asset_type=asset.type,
                    metrics=metrics,
                    alerts=alerts,
                    health=float(asset.health),
                    condition=str(profile.get("condition") or ""),
                )
            )

        # Deduplicate by rule+asset, keep highest priority/confidence.
        merged: dict[str, RecommendationDraft] = {}
        for draft in drafts:
            key = f"{draft.rule_code}:{draft.affected_asset}"
            existing = merged.get(key)
            if existing is None or self._priority_rank(draft.priority) > self._priority_rank(
                existing.priority
            ):
                merged[key] = draft

        return sorted(
            merged.values(),
            key=lambda d: (-self._priority_rank(d.priority), -d.confidence, d.title),
        )

    def maintenance_prediction_rules(
        self,
        predictions: list[dict[str, Any]],
    ) -> list[RecommendationDraft]:
        """
        Translate ML risk scores into deterministic operational drafts.

        RecommendationService remains the single assembly point. The ML model
        supplies risk evidence; this service owns the engineering action rule.
        """
        drafts: list[RecommendationDraft] = []
        for prediction in predictions:
            risk = float(prediction.get("maintenance_risk", 0.0))
            if risk < 0.40:
                continue

            risk_level = str(prediction.get("risk_level", "medium"))
            if risk_level == "critical":
                priority = severity = "critical"
            elif risk_level == "high":
                priority = severity = "high"
            else:
                priority = severity = "medium"

            contributors = prediction.get("top_contributing_features") or []
            reasons = ", ".join(
                str(item.get("feature", "")).replace("_", " ")
                for item in contributors[:3]
            )
            asset_name = str(
                prediction.get("asset_name") or prediction.get("asset_id") or "pump asset"
            )
            failure_days = int(prediction.get("predicted_failure_days", 1))
            day_label = "day" if failure_days == 1 else "days"
            drafts.append(
                RecommendationDraft(
                    rule_code="xgboost_maintenance_risk",
                    priority=priority,
                    severity=severity,
                    title=f"Predictive maintenance risk - {asset_name}",
                    description=(
                        f"XGBoost maintenance risk is {risk:.0%}; estimated failure "
                        f"horizon is {failure_days} {day_label}."
                        + (f" Main contributors: {reasons}." if reasons else "")
                    ),
                    affected_asset=str(prediction.get("asset_id", "")),
                    affected_zone=str(prediction.get("affected_zone", "")),
                    recommendation=(
                        "Schedule condition-based inspection, verify pump alignment and "
                        "controls, and prepare the standby unit before the predicted horizon."
                    ),
                    estimated_business_impact=(
                        "Avoids unplanned pump outage and associated zone supply disruption"
                    ),
                    # Track risk probability — prediction["confidence"] is certainty
                    # (distance from 0.5), which is high even when risk is near 0.
                    confidence=risk,
                )
            )
        return drafts

    def power_maintenance_prediction_rules(
        self,
        predictions: list[dict[str, Any]],
    ) -> list[RecommendationDraft]:
        """
        Translate Power transformer XGBoost risk into model-backed drafts.

        Explicitly distinct from rule-based ``power_*`` telemetry recommendations.
        """
        drafts: list[RecommendationDraft] = []
        for prediction in predictions:
            risk = float(prediction.get("maintenance_risk", 0.0))
            if risk < 0.40:
                continue

            risk_level = str(prediction.get("risk_level", "medium"))
            if risk_level == "critical":
                priority = severity = "critical"
            elif risk_level == "high":
                priority = severity = "high"
            else:
                priority = severity = "medium"

            contributors = prediction.get("top_contributing_features") or []
            reasons = ", ".join(
                str(item.get("feature", "")).replace("_", " ")
                for item in contributors[:3]
            )
            asset_name = str(
                prediction.get("asset_name") or prediction.get("asset_id") or "transformer"
            )
            model_version = str(prediction.get("model_version") or "transformer_xgboost")
            evidence = prediction.get("feature_evidence") or {}
            evidence_bits = []
            for key in ("oil_temp_c", "load_pct", "voltage_kv", "power_factor"):
                if evidence.get(key) is not None:
                    evidence_bits.append(f"{key}={evidence[key]}")
            evidence_text = "; ".join(evidence_bits)

            certainty = float(prediction.get("model_certainty", 0.5))
            drafts.append(
                RecommendationDraft(
                    rule_code="power_xgboost_maintenance_risk",
                    priority=priority,
                    severity=severity,
                    title=f"Model-based transformer maintenance risk - {asset_name}",
                    description=(
                        f"Predictive maintenance model ({model_version}) risk="
                        f"{risk:.0%} (assessment_kind=predictive_maintenance_model; "
                        f"model_certainty={certainty:.0%} = distance from 0.5, not accuracy)."
                        + (f" Contributors: {reasons}." if reasons else "")
                        + (f" Observed: {evidence_text}." if evidence_text else "")
                    ),
                    affected_asset=str(prediction.get("asset_id", "")),
                    affected_zone=str(prediction.get("affected_zone", "")),
                    recommendation=(
                        "Schedule transformer condition-based inspection; review oil "
                        "temperature and sustained loading trends indicated by the model; "
                        "correlate with rule-based Power condition alerts before intrusive work."
                    ),
                    estimated_business_impact=(
                        "Reduces unplanned transformer outage risk under sustained stress."
                    ),
                    # Toast / AI "confidence" for this rule must track risk probability,
                    # not model_certainty (which is high when risk is near 0 or 1).
                    confidence=risk,
                )
            )
        return drafts

    def gas_maintenance_prediction_rules(
        self,
        predictions: list[dict[str, Any]],
    ) -> list[RecommendationDraft]:
        """
        Translate Gas XGBoost risk into model-backed drafts.

        Explicitly distinct from rule-based ``gas_*`` telemetry recommendations.
        """
        drafts: list[RecommendationDraft] = []
        for prediction in predictions:
            risk = float(prediction.get("maintenance_risk", 0.0))
            if risk < 0.40:
                continue

            risk_level = str(prediction.get("risk_level", "medium"))
            if risk_level == "critical":
                priority = severity = "critical"
            elif risk_level == "high":
                priority = severity = "high"
            else:
                priority = severity = "medium"

            contributors = prediction.get("top_contributing_features") or []
            reasons = ", ".join(
                str(item.get("feature", "")).replace("_", " ")
                for item in contributors[:3]
            )
            asset_name = str(
                prediction.get("asset_name") or prediction.get("asset_id") or "gas asset"
            )
            model_version = str(prediction.get("model_version") or "gas_xgboost")
            evidence = prediction.get("feature_evidence") or {}
            evidence_bits = []
            for key in (
                "compressor_load_pct",
                "vibration_mm_s",
                "leak_indicator",
                "pressure_bar",
                "temperature_c",
                "gas_quality_pct",
            ):
                if evidence.get(key) is not None:
                    evidence_bits.append(f"{key}={evidence[key]}")
            evidence_text = "; ".join(evidence_bits)
            certainty = float(prediction.get("model_certainty", 0.5))

            drafts.append(
                RecommendationDraft(
                    rule_code="gas_xgboost_maintenance_risk",
                    priority=priority,
                    severity=severity,
                    title=f"Model-based Gas maintenance risk - {asset_name}",
                    description=(
                        f"Predictive maintenance model ({model_version}) risk="
                        f"{risk:.0%} (assessment_kind=predictive_maintenance_model; "
                        f"model_certainty={certainty:.0%} = distance from 0.5, not accuracy). "
                        "Simulator-derived POC supervision — not certified plant prediction."
                        + (f" Contributors: {reasons}." if reasons else "")
                        + (f" Observed: {evidence_text}." if evidence_text else "")
                    ),
                    affected_asset=str(prediction.get("asset_id", "")),
                    affected_zone=str(prediction.get("affected_zone", "")),
                    recommendation=(
                        "Schedule Gas condition-based inspection; review leak indicator, "
                        "load/vibration (compressors), and pressure/flow trends indicated by "
                        "the model; correlate with rule-based Gas condition alerts before "
                        "intrusive work."
                    ),
                    estimated_business_impact=(
                        "Reduces unplanned Gas asset outage risk under sustained stress."
                    ),
                    confidence=risk,
                )
            )
        return drafts

    def _power_telemetry_rules(
        self,
        *,
        asset_id: str,
        asset_name: str,
        zone_id: str,
        asset_type: str,
        metrics: dict[str, float],
        alerts: list[str],
        health: float,
        condition: str,
    ) -> list[RecommendationDraft]:
        """Rule-based Power operational recommendations — not ML predictions."""
        out: list[RecommendationDraft] = []
        condition_note = condition or power_health.condition_from_alerts(
            alerts, has_telemetry=bool(metrics)
        )

        def _impact(text: str) -> str:
            return text  # Qualitative only — no fabricated currency figures.

        if "oil_temp_critical" in alerts or "load_critical" in alerts:
            out.append(
                RecommendationDraft(
                    rule_code="power_critical_condition",
                    priority="critical",
                    severity="critical",
                    title="Critical Power asset condition",
                    description=(
                        f"Rule-based condition assessment for {asset_name} "
                        f"({asset_type}) in {zone_id}: state={condition_note}. "
                        f"load_pct={metrics.get('load_pct', float('nan')):.1f}, "
                        f"oil_temp_c={metrics.get('oil_temp_c', float('nan')):.1f}, "
                        f"voltage_kv={metrics.get('voltage_kv', float('nan')):.2f}."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Inspect the affected Power asset immediately; review sustained "
                        "loading and thermal condition; prepare contingency switching if needed."
                    ),
                    estimated_business_impact=_impact(
                        "Elevated service disruption risk if condition continues."
                    ),
                    confidence=0.88,
                )
            )
        elif "thermal_loading_stress" in alerts or "oil_temp_high" in alerts:
            out.append(
                RecommendationDraft(
                    rule_code="power_transformer_thermal_stress",
                    priority="high",
                    severity="high",
                    title="Transformer thermal / loading stress",
                    description=(
                        f"Rule-based assessment: {asset_name} shows combined thermal and "
                        f"loading stress (condition={condition_note}). "
                        f"oil_temp_c={metrics.get('oil_temp_c', float('nan')):.1f}, "
                        f"load_pct={metrics.get('load_pct', float('nan')):.1f}."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Inspect transformer cooling and thermal condition; review sustained "
                        "loading; schedule a maintenance inspection if the trend continues."
                    ),
                    estimated_business_impact=_impact(
                        "Accelerated insulation aging risk under sustained thermal stress."
                    ),
                    confidence=0.82,
                )
            )
        elif "load_high" in alerts or "load_rising" in alerts:
            out.append(
                RecommendationDraft(
                    rule_code="power_high_loading",
                    priority="high",
                    severity="high",
                    title="Elevated Power loading",
                    description=(
                        f"Rule-based assessment: {asset_name} loading is elevated "
                        f"(load_pct={metrics.get('load_pct', float('nan')):.1f}, "
                        f"condition={condition_note})."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Review feeder/transformer loading; investigate sustained current "
                        "stress; consider load redistribution if operationally feasible."
                    ),
                    estimated_business_impact=_impact(
                        "Increased equipment stress under sustained high loading."
                    ),
                    confidence=0.78,
                )
            )
        elif "voltage_low" in alerts or "voltage_critical_low" in alerts:
            out.append(
                RecommendationDraft(
                    rule_code="power_voltage_sag",
                    priority="high" if "voltage_critical_low" in alerts else "medium",
                    severity="high" if "voltage_critical_low" in alerts else "medium",
                    title="Power voltage deviation",
                    description=(
                        f"Rule-based assessment: {asset_name} voltage "
                        f"{metrics.get('voltage_kv', float('nan')):.2f} kV is below "
                        f"operational band (condition={condition_note})."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Investigate voltage regulation and upstream supply; correlate with "
                        "loading on the same feeder/transformer."
                    ),
                    estimated_business_impact=_impact(
                        "Voltage quality risk for downstream loads."
                    ),
                    confidence=0.80,
                )
            )
        elif any(
            c in alerts
            for c in (
                "oil_temp_elevated",
                "oil_temp_rising",
                "load_elevated",
                "power_factor_low",
            )
        ):
            out.append(
                RecommendationDraft(
                    rule_code="power_condition_warning",
                    priority="medium",
                    severity="medium",
                    title="Power condition warning",
                    description=(
                        f"Rule-based assessment: {asset_name} shows early stress indicators "
                        f"(condition={condition_note}; drivers={', '.join(alerts)})."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Increase monitoring frequency; verify loading and thermal telemetry "
                        "trends before scheduling intrusive maintenance."
                    ),
                    estimated_business_impact=_impact(
                        "Early-warning operational stress; monitor for progression."
                    ),
                    confidence=0.72,
                )
            )

        return out

    def _gas_telemetry_rules(
        self,
        *,
        asset_id: str,
        asset_name: str,
        zone_id: str,
        asset_type: str,
        metrics: dict[str, float],
        alerts: list[str],
        health: float,
        condition: str,
    ) -> list[RecommendationDraft]:
        """Rule-based Gas operational recommendations — not ML predictions.

        One primary draft per asset (elif cascade), driven by Phase 15.1 condition
        codes. Compressor-only codes never fire on REG/GFDR because evaluate_alerts
        is asset-type gated.
        """
        out: list[RecommendationDraft] = []
        condition_note = condition or gas_health.condition_from_alerts(
            alerts, has_telemetry=bool(metrics)
        )
        is_compressor = asset_type == "gas_compressor"

        def _impact(text: str) -> str:
            return text

        def _metric_snip() -> str:
            parts: list[str] = []
            for key in (
                "pressure_bar",
                "flow_rate_nm3_h",
                "temperature_c",
                "compressor_load_pct",
                "vibration_mm_s",
                "leak_indicator",
                "gas_quality_pct",
                "valve_position_pct",
                "compressor_discharge_pressure_bar",
                "power_consumption_kw",
            ):
                if key in metrics:
                    parts.append(f"{key}={metrics[key]:.3f}")
            return ", ".join(parts) if parts else "no live metrics"

        critical_codes = {
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

        if any(c in alerts for c in critical_codes):
            out.append(
                RecommendationDraft(
                    rule_code="gas_critical_condition",
                    priority="critical",
                    severity="critical",
                    title="Critical Gas asset condition",
                    description=(
                        f"Rule-based assessment for {asset_name} ({asset_type}) in "
                        f"{zone_id}: state={condition_note}. Evidence: {_metric_snip()}. "
                        f"Codes: {', '.join(alerts)}."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Inspect the affected Gas asset immediately; verify pressure, "
                        "flow, leak indicator, and (for compressors) load/vibration; "
                        "prepare isolation/contingency if the condition persists."
                    ),
                    estimated_business_impact=_impact(
                        "Elevated gas-service disruption risk if critical condition continues."
                    ),
                    confidence=0.88,
                )
            )
        elif is_compressor and "compressor_mechanical_stress" in alerts:
            out.append(
                RecommendationDraft(
                    rule_code="gas_compressor_mechanical_stress",
                    priority="high",
                    severity="high",
                    title="Compressor mechanical / loading stress",
                    description=(
                        f"Rule-based assessment: {asset_name} shows combined compressor "
                        f"load and vibration stress (condition={condition_note}). "
                        f"{_metric_snip()}."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Inspect compressor mechanical condition and mounting; review "
                        "sustained load and vibration trends; schedule a maintenance "
                        "inspection if the stress continues."
                    ),
                    estimated_business_impact=_impact(
                        "Accelerated mechanical wear risk under combined load and vibration stress."
                    ),
                    confidence=0.84,
                )
            )
        elif is_compressor and any(
            c in alerts
            for c in (
                "compressor_load_high",
                "compressor_load_rising",
                "power_consumption_high",
                "discharge_pressure_high",
            )
        ):
            out.append(
                RecommendationDraft(
                    rule_code="gas_compressor_overload",
                    priority="high",
                    severity="high",
                    title="Elevated compressor loading / duty",
                    description=(
                        f"Rule-based assessment: {asset_name} compressor duty is elevated "
                        f"(condition={condition_note}). {_metric_snip()}."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Review compressor load and discharge pressure versus nominal; "
                        "investigate sustained duty stress; consider load redistribution "
                        "if operationally feasible."
                    ),
                    estimated_business_impact=_impact(
                        "Increased compressor stress under sustained high duty."
                    ),
                    confidence=0.80,
                )
            )
        elif is_compressor and any(
            c in alerts for c in ("vibration_high", "vibration_rising", "vibration_elevated")
        ):
            out.append(
                RecommendationDraft(
                    rule_code="gas_vibration_elevated",
                    priority="high" if "vibration_high" in alerts else "medium",
                    severity="high" if "vibration_high" in alerts else "medium",
                    title="Elevated Gas compressor vibration",
                    description=(
                        f"Rule-based assessment: {asset_name} vibration is elevated "
                        f"(condition={condition_note}). {_metric_snip()}."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Inspect rotating equipment balance/alignment and foundation; "
                        "correlate vibration with load; increase monitoring frequency."
                    ),
                    estimated_business_impact=_impact(
                        "Mechanical integrity risk if vibration continues to rise."
                    ),
                    confidence=0.80,
                )
            )
        elif any(
            c in alerts
            for c in ("leak_indicator_high", "leak_indicator_elevated", "leak_indicator_rising")
        ):
            out.append(
                RecommendationDraft(
                    rule_code="gas_leak_indicator_elevated",
                    priority="high" if "leak_indicator_high" in alerts else "medium",
                    severity="high" if "leak_indicator_high" in alerts else "medium",
                    title="Elevated Gas leak indicator (POC)",
                    description=(
                        f"Rule-based assessment: {asset_name} leak indicator is elevated "
                        f"(POC synthetic signal; condition={condition_note}). "
                        f"{_metric_snip()}."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Perform a field leak survey around the asset; verify fittings "
                        "and seals; treat the indicator as a POC cue, not certified CH₄."
                    ),
                    estimated_business_impact=_impact(
                        "Potential integrity / safety concern if a real leak is confirmed."
                    ),
                    confidence=0.78,
                )
            )
        elif any(
            c in alerts
            for c in (
                "pressure_high",
                "pressure_elevated",
                "flow_high",
                "flow_elevated",
            )
        ):
            out.append(
                RecommendationDraft(
                    rule_code="gas_pressure_flow_abnormal",
                    priority="high"
                    if any(c in alerts for c in ("pressure_high", "flow_high"))
                    else "medium",
                    severity="high"
                    if any(c in alerts for c in ("pressure_high", "flow_high"))
                    else "medium",
                    title="Abnormal Gas pressure / flow",
                    description=(
                        f"Rule-based assessment: {asset_name} pressure or flow deviates "
                        f"from nominal (condition={condition_note}). {_metric_snip()}."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Compare live pressure and flow to asset nominals; inspect "
                        "upstream/downstream regulation and possible restrictions or leaks."
                    ),
                    estimated_business_impact=_impact(
                        "Delivery quality / continuity risk under sustained deviation."
                    ),
                    confidence=0.78,
                )
            )
        elif any(
            c in alerts
            for c in (
                "temperature_high",
                "temperature_elevated",
                "temperature_rising",
            )
        ):
            out.append(
                RecommendationDraft(
                    rule_code="gas_temperature_elevated",
                    priority="high" if "temperature_high" in alerts else "medium",
                    severity="high" if "temperature_high" in alerts else "medium",
                    title="Elevated Gas process temperature",
                    description=(
                        f"Rule-based assessment: {asset_name} temperature is elevated "
                        f"(condition={condition_note}). {_metric_snip()}."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Verify process temperature sensors and cooling/heat-exchange "
                        "condition; correlate with compressor duty where applicable."
                    ),
                    estimated_business_impact=_impact(
                        "Thermal stress risk if temperature remains elevated."
                    ),
                    confidence=0.76,
                )
            )
        elif any(
            c in alerts
            for c in ("gas_quality_high", "gas_quality_elevated")
        ):
            out.append(
                RecommendationDraft(
                    rule_code="gas_quality_degraded",
                    priority="high" if "gas_quality_high" in alerts else "medium",
                    severity="high" if "gas_quality_high" in alerts else "medium",
                    title="Degraded gas quality",
                    description=(
                        f"Rule-based assessment: {asset_name} gas quality is below "
                        f"operating band (condition={condition_note}). {_metric_snip()}."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Review gas quality telemetry and upstream supply quality; "
                        "confirm sensor calibration before escalating process changes."
                    ),
                    estimated_business_impact=_impact(
                        "Downstream process quality risk under sustained degradation."
                    ),
                    confidence=0.74,
                )
            )
        elif asset_type == "gas_regulator_station" and any(
            c in alerts for c in ("valve_position_high", "valve_position_elevated")
        ):
            out.append(
                RecommendationDraft(
                    rule_code="gas_valve_abnormal",
                    priority="high" if "valve_position_high" in alerts else "medium",
                    severity="high" if "valve_position_high" in alerts else "medium",
                    title="Regulator valve position abnormality",
                    description=(
                        f"Rule-based assessment: {asset_name} valve position is near "
                        f"or beyond operating edges (condition={condition_note}). "
                        f"{_metric_snip()}."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Inspect regulator valve actuation and setpoints; verify "
                        "downstream demand/pressure; avoid forcing extreme positions."
                    ),
                    estimated_business_impact=_impact(
                        "Regulation instability risk if valve remains at extreme position."
                    ),
                    confidence=0.76,
                )
            )
        elif any(
            c in alerts
            for c in (
                "compressor_load_elevated",
                "discharge_pressure_elevated",
                "power_consumption_elevated",
            )
        ):
            # Compressor early warnings only (codes already compressor-gated).
            if not is_compressor:
                return out
            out.append(
                RecommendationDraft(
                    rule_code="gas_condition_warning",
                    priority="medium",
                    severity="medium",
                    title="Gas compressor condition warning",
                    description=(
                        f"Rule-based assessment: {asset_name} shows early stress "
                        f"indicators (condition={condition_note}; "
                        f"drivers={', '.join(alerts)})."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Increase monitoring frequency; verify load, discharge pressure, "
                        "and power consumption trends before scheduling intrusive work."
                    ),
                    estimated_business_impact=_impact(
                        "Early-warning operational stress; monitor for progression."
                    ),
                    confidence=0.72,
                )
            )

        return out

    def _communications_telemetry_rules(
        self,
        *,
        asset_id: str,
        asset_name: str,
        zone_id: str,
        asset_type: str,
        metrics: dict[str, float],
        alerts: list[str],
        health: float,
        condition: str,
    ) -> list[RecommendationDraft]:
        """Rule-based Communications operational recommendations — not ML."""
        del health  # score available for future weighting; rules use findings
        out: list[RecommendationDraft] = []
        condition_note = condition or communications_health.condition_from_alerts(
            alerts,
            has_telemetry=bool(metrics),
            link_up=metrics.get("link_up"),
        )
        link_up = metrics.get("link_up")
        link_available = link_up is None or float(link_up) >= 0.5

        def _impact(text: str) -> str:
            return text

        def _metric_snip() -> str:
            return (
                f"link_up={metrics.get('link_up', float('nan'))}, "
                f"link_rtt_ms={metrics.get('link_rtt_ms', float('nan')):.1f}, "
                f"packet_loss_pct={metrics.get('packet_loss_pct', float('nan')):.2f}, "
                f"cabinet_temp_c={metrics.get('cabinet_temp_c', float('nan')):.1f}, "
                f"power_draw_w={metrics.get('power_draw_w', float('nan')):.1f}"
            )

        if "link_down" in alerts or condition_note == "unavailable":
            out.append(
                RecommendationDraft(
                    rule_code="communications_link_outage",
                    priority="critical",
                    severity="critical",
                    title="Communications link outage",
                    description=(
                        f"Rule-based assessment: {asset_name} ({asset_type}) in {zone_id} "
                        f"reports path unavailable (condition={condition_note}). "
                        f"{_metric_snip()}."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        f"Confirm the path/outage on {asset_id}; correlate last-known RTT "
                        "and packet loss with the power state; restore the link before "
                        "trusting other sensor signals from this asset."
                    ),
                    estimated_business_impact=_impact(
                        "Corridor telemetry/control path risk while the link remains down."
                    ),
                    confidence=0.90,
                )
            )
        elif link_available and (
            "rtt_critical" in alerts or "packet_loss_critical" in alerts
        ):
            out.append(
                RecommendationDraft(
                    rule_code="communications_critical_path",
                    priority="critical",
                    severity="critical",
                    title="Critical Communications path quality",
                    description=(
                        f"Rule-based assessment: {asset_name} shows critical latency "
                        f"and/or packet loss (condition={condition_note}). {_metric_snip()}."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        f"Inspect uplink/path quality for {asset_id}; correlate latency "
                        "and packet loss; prioritize path restoration."
                    ),
                    estimated_business_impact=_impact(
                        "Elevated risk of telemetry loss or control degradation."
                    ),
                    confidence=0.88,
                )
            )
        elif "path_quality_stress" in alerts:
            out.append(
                RecommendationDraft(
                    rule_code="communications_path_quality_stress",
                    priority="high",
                    severity="high",
                    title="Communications path quality stress",
                    description=(
                        f"Rule-based assessment: {asset_name} shows combined RTT and "
                        f"packet-loss stress (condition={condition_note}). {_metric_snip()}."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        f"Investigate combined latency and packet loss on {asset_id}; "
                        "compare adjacent communications assets in the same zone for a "
                        "shared path pattern."
                    ),
                    estimated_business_impact=_impact(
                        "Degraded path quality may progress to outage if sustained."
                    ),
                    confidence=0.84,
                )
            )
        elif "cabinet_temp_critical" in alerts or "cabinet_temp_high" in alerts:
            out.append(
                RecommendationDraft(
                    rule_code="communications_cabinet_thermal",
                    priority="critical" if "cabinet_temp_critical" in alerts else "high",
                    severity="critical" if "cabinet_temp_critical" in alerts else "high",
                    title="Communications cabinet thermal stress",
                    description=(
                        f"Rule-based assessment: {asset_name} cabinet temperature is "
                        f"elevated (condition={condition_note}). {_metric_snip()}."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        f"Inspect cabinet cooling/ventilation at {asset_id}; verify the "
                        "temperature trend before intrusive field work."
                    ),
                    estimated_business_impact=_impact(
                        "Thermal stress risk to edge communications equipment (POC bands)."
                    ),
                    confidence=0.82,
                )
            )
        elif "rtt_high" in alerts or (
            "rtt_elevated" in alerts and "rtt_rising" in alerts
        ):
            out.append(
                RecommendationDraft(
                    rule_code="communications_rtt_degraded",
                    priority="high" if "rtt_high" in alerts else "medium",
                    severity="high" if "rtt_high" in alerts else "medium",
                    title="Communications latency degraded",
                    description=(
                        f"Rule-based assessment: {asset_name} RTT is degraded vs nominal "
                        f"(condition={condition_note}). {_metric_snip()}."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        f"Review path congestion/failover for {asset_id}; compare current "
                        "RTT against the asset's nominal_link_rtt_ms."
                    ),
                    estimated_business_impact=_impact(
                        "Increased latency may delay SCADA/IoT telemetry delivery."
                    ),
                    confidence=0.78,
                )
            )
        elif "packet_loss_high" in alerts or (
            "packet_loss_elevated" in alerts and "packet_loss_rising" in alerts
        ):
            out.append(
                RecommendationDraft(
                    rule_code="communications_packet_loss",
                    priority="high" if "packet_loss_high" in alerts else "medium",
                    severity="high" if "packet_loss_high" in alerts else "medium",
                    title="Communications packet loss elevated",
                    description=(
                        f"Rule-based assessment: {asset_name} packet loss is elevated "
                        f"(condition={condition_note}). {_metric_snip()}."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        f"Check link quality/error conditions toward {asset_id}; continue "
                        "monitoring if loss persists before scheduling field dispatch."
                    ),
                    estimated_business_impact=_impact(
                        "Packet loss may corrupt or drop telemetry samples."
                    ),
                    confidence=0.78,
                )
            )
        elif "power_draw_high" in alerts and link_available:
            out.append(
                RecommendationDraft(
                    rule_code="communications_power_anomaly",
                    priority="high",
                    severity="high",
                    title="Communications power draw anomaly",
                    description=(
                        f"Rule-based assessment: {asset_name} power draw deviates from "
                        f"nominal while the link remains available "
                        f"(condition={condition_note}). {_metric_snip()}."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        f"Verify power draw on {asset_id} against nominal_power_draw_w "
                        "while the link remains available; rule out a load or metering anomaly."
                    ),
                    estimated_business_impact=_impact(
                        "Abnormal power draw may precede thermal or availability issues."
                    ),
                    confidence=0.74,
                )
            )
        elif any(
            c in alerts
            for c in (
                "rtt_elevated",
                "rtt_rising",
                "packet_loss_elevated",
                "packet_loss_rising",
                "cabinet_temp_elevated",
                "cabinet_temp_rising",
                "power_draw_elevated",
            )
        ):
            out.append(
                RecommendationDraft(
                    rule_code="communications_condition_warning",
                    priority="medium",
                    severity="medium",
                    title="Communications condition warning",
                    description=(
                        f"Rule-based assessment: {asset_name} shows early path/thermal "
                        f"indicators (condition={condition_note}; "
                        f"drivers={', '.join(alerts)})."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        f"Increase monitoring on {asset_id}; re-check after the next "
                        "telemetry window before considering field dispatch."
                    ),
                    estimated_business_impact=_impact(
                        "Early-warning communications stress; monitor for progression."
                    ),
                    confidence=0.70,
                )
            )

        return out

    def _telemetry_rules(
        self,
        *,
        asset_id: str,
        asset_name: str,
        zone_id: str,
        asset_type: str,
        metrics: dict[str, float],
        alerts: list[str],
        health: float,
    ) -> list[RecommendationDraft]:
        out: list[RecommendationDraft] = []

        if "pressure_critical_low" in alerts:
            out.append(
                RecommendationDraft(
                    rule_code="pressure_critical",
                    priority="critical",
                    severity="critical",
                    title="Critical pressure loss detected",
                    description=(
                        f"{asset_name} reports pressure {metrics.get('pressure', 0):.2f} bar, "
                        "below the critical threshold."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Dispatch field crew immediately; activate backup pumping and "
                        "isolate affected distribution segments."
                    ),
                    estimated_business_impact="₹1.2–2.8Cr/day service disruption risk",
                    confidence=0.94,
                )
            )
        elif "pressure_low" in alerts:
            out.append(
                RecommendationDraft(
                    rule_code="pressure_low",
                    priority="high",
                    severity="high",
                    title="Low distribution pressure",
                    description=(
                        f"{asset_name} pressure at {metrics.get('pressure', 0):.2f} bar "
                        "is trending below operational minimum."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Increase pump setpoints, verify valve positions, and inspect "
                        "for upstream flow restrictions."
                    ),
                    estimated_business_impact="₹35–80L/day elevated pump energy + supply risk",
                    confidence=0.88,
                )
            )

        if "level_critical" in alerts:
            out.append(
                RecommendationDraft(
                    rule_code="level_critical",
                    priority="critical",
                    severity="critical",
                    title="Critical storage depletion",
                    description=(
                        f"{asset_name} storage level critically low "
                        f"({metrics.get('tank_level') or metrics.get('reservoir_level') or level_fallback(metrics):.1f}%)."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Enact demand curtailment, reroute inflow from adjacent zones, "
                        "and schedule emergency tanker support."
                    ),
                    estimated_business_impact="₹90L–1.5Cr/day if storage reaches empty",
                    confidence=0.92,
                )
            )
        elif "level_low" in alerts:
            out.append(
                RecommendationDraft(
                    rule_code="level_low",
                    priority="high",
                    severity="high",
                    title="Storage level declining",
                    description=f"{asset_name} storage is below the low-level threshold.",
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Reduce non-essential withdrawals, increase source inflow, "
                        "and monitor draw rate hourly."
                    ),
                    estimated_business_impact="₹20–45L/day if draw continues unchecked",
                    confidence=0.85,
                )
            )

        if "pump_stopped" in alerts and asset_type == "pump_station":
            out.append(
                RecommendationDraft(
                    rule_code="pump_stopped",
                    priority="critical",
                    severity="critical",
                    title="Pump station offline",
                    description=(
                        f"{asset_name} pump is not running "
                        f"(speed {metrics.get('pump_speed', 0):.0f} rpm)."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Dispatch maintenance crew, start standby pump, and verify "
                        "power/control circuit status."
                    ),
                    estimated_business_impact="₹1.5–3Cr/day zone supply failure",
                    confidence=0.91,
                )
            )

        if "flow_low" in alerts:
            out.append(
                RecommendationDraft(
                    rule_code="flow_low",
                    priority="high",
                    severity="high",
                    title="Abnormally low flow",
                    description=(
                        f"{asset_name} flow rate {metrics.get('flow_rate', 0):.0f} L/s "
                        "is below corridor minimum."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Inspect upstream pumps and valves; check for blockages or "
                        "partial line closure."
                    ),
                    estimated_business_impact="₹25–60L/day industrial supply shortfall",
                    confidence=0.84,
                )
            )

        if "turbidity_high" in alerts:
            out.append(
                RecommendationDraft(
                    rule_code="turbidity_high",
                    priority="critical",
                    severity="critical",
                    title="Water quality excursion — turbidity",
                    description=(
                        f"{asset_name} turbidity at {metrics.get('turbidity', 0):.2f} NTU "
                        "exceeds treatment limits."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Increase coagulation dose, divert flow to holding, and hold "
                        "distribution until quality clears."
                    ),
                    estimated_business_impact="₹2–5Cr regulatory + health liability",
                    confidence=0.93,
                )
            )

        if health < 70 and not alerts:
            out.append(
                RecommendationDraft(
                    rule_code="health_degraded",
                    priority="medium",
                    severity="medium",
                    title="Asset health trending down",
                    description=(
                        f"{asset_name} composite health score is {health:.1f}% "
                        "without a single dominant alert."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Schedule preventive inspection and correlate multi-metric "
                        "trends over the next 6 hours."
                    ),
                    estimated_business_impact="₹8–15L/day if degradation accelerates",
                    confidence=0.72,
                )
            )

        return out

    def _scenario_rules(
        self,
        *,
        scenario: ScenarioDefinition,
        intensity: float,
        asset_id: str,
        asset_name: str,
        zone_id: str,
        asset_type: str,
        metrics: dict[str, float],
        health: float,
    ) -> list[RecommendationDraft]:
        if intensity < 0.15:
            return []

        key = scenario.key
        out: list[RecommendationDraft] = []

        if key == ScenarioKey.PEAK_DEMAND and asset_type in {"pump_station", "distribution_network"}:
            out.append(
                RecommendationDraft(
                    rule_code="scenario_peak_demand",
                    priority="medium" if intensity < 0.6 else "high",
                    severity="medium" if intensity < 0.6 else "high",
                    title="Peak demand load balancing",
                    description=(
                        f"Peak demand scenario active ({intensity * 100:.0f}% intensity). "
                        f"{asset_name} showing elevated throughput."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Stage additional pump capacity, defer non-critical withdrawals, "
                        "and monitor pressure at zone boundaries."
                    ),
                    estimated_business_impact="₹12–30L/day excess energy if unmanaged",
                    confidence=min(0.9, 0.65 + intensity * 0.25),
                )
            )

        if key == ScenarioKey.PUMP_FAILURE and asset_type == "pump_station":
            out.append(
                RecommendationDraft(
                    rule_code="scenario_pump_failure",
                    priority="critical" if intensity >= 0.4 else "high",
                    severity="critical" if intensity >= 0.4 else "high",
                    title="Simulated pump failure — contingency required",
                    description=(
                        f"Pump failure scenario ramping ({intensity * 100:.0f}%). "
                        f"{asset_name} metrics degrading."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Isolate failed pump, energize standby unit, and reconfigure "
                        "distribution to maintain minimum service pressure."
                    ),
                    estimated_business_impact="₹1.8–4Cr/day if redundancy not activated",
                    confidence=min(0.96, 0.7 + intensity * 0.26),
                )
            )

        if key == ScenarioKey.PIPELINE_LEAK and asset_type in {
            "reservoir",
            "distribution_network",
            "flow_meter",
        }:
            out.append(
                RecommendationDraft(
                    rule_code="scenario_pipeline_leak",
                    priority="high" if intensity < 0.7 else "critical",
                    severity="high" if intensity < 0.7 else "critical",
                    title="Suspected pipeline leak under scenario stress",
                    description=(
                        f"Pipeline leak scenario ({intensity * 100:.0f}%). "
                        f"Outflow/pressure imbalance detected at {asset_name}."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Run acoustic leak survey on downstream mains, sectionalize "
                        "suspect segment, and reduce system pressure temporarily."
                    ),
                    estimated_business_impact="₹40L–1.2Cr/day water loss + road damage risk",
                    confidence=min(0.94, 0.68 + intensity * 0.26),
                )
            )

        if key == ScenarioKey.RESERVOIR_LOW and asset_type in {"storage_tank", "reservoir"}:
            out.append(
                RecommendationDraft(
                    rule_code="scenario_reservoir_low",
                    priority="high" if intensity < 0.65 else "critical",
                    severity="high" if intensity < 0.65 else "critical",
                    title="Reservoir drawdown scenario active",
                    description=(
                        f"Low storage scenario ({intensity * 100:.0f}%) affecting {asset_name}."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Implement zone-level demand caps, increase source pumping windows, "
                        "and alert industrial offtakers."
                    ),
                    estimated_business_impact="₹55L–1.1Cr/day if storage not stabilized",
                    confidence=min(0.93, 0.66 + intensity * 0.27),
                )
            )

        if key == ScenarioKey.CONTAMINATION and asset_type == "water_treatment_plant":
            out.append(
                RecommendationDraft(
                    rule_code="scenario_contamination",
                    priority="critical",
                    severity="critical",
                    title="Contamination scenario — quality hold required",
                    description=(
                        f"Water contamination scenario ({intensity * 100:.0f}%) at {asset_name}."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Issue quality hold, increase chlorine contact time, notify "
                        "regulator, and prepare consumer advisory templates."
                    ),
                    estimated_business_impact="₹3–8Cr regulatory, health, and reputational",
                    confidence=min(0.97, 0.75 + intensity * 0.22),
                )
            )

        if key == ScenarioKey.HEAVY_RAIN and asset_type in {"reservoir", "water_treatment_plant"}:
            out.append(
                RecommendationDraft(
                    rule_code="scenario_heavy_rain",
                    priority="high",
                    severity="high",
                    title="Storm inflow surge management",
                    description=(
                        f"Heavy rain/flood scenario ({intensity * 100:.0f}%) stressing {asset_name}."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Open spillway controls gradually, pre-position mobile pumps, "
                        "and increase treatment chemical stock."
                    ),
                    estimated_business_impact="₹30–90L/day treatment + flood defence costs",
                    confidence=min(0.91, 0.64 + intensity * 0.27),
                )
            )

        if key == ScenarioKey.POWER_OUTAGE and asset_type == "pump_station":
            out.append(
                RecommendationDraft(
                    rule_code="scenario_power_outage",
                    priority="critical",
                    severity="critical",
                    title="Power outage — pump station down",
                    description=(
                        f"Power outage scenario ({intensity * 100:.0f}%). "
                        f"{asset_name} losing motive power."
                    ),
                    affected_asset=asset_id,
                    affected_zone=zone_id,
                    recommendation=(
                        "Start diesel generators, prioritize critical pump feeders, "
                        "and notify industrial customers of supply curtailment."
                    ),
                    estimated_business_impact="₹2–6Cr/day full-zone outage",
                    confidence=min(0.98, 0.78 + intensity * 0.2),
                )
            )

        return out

    def weather_impact_rules(
        self,
        *,
        impact_service: WeatherImpactService | None = None,
        forecast_hours: int = 48,
    ) -> list[RecommendationDraft]:
        """
        Map deterministic WeatherImpactService signals to recommendation drafts.

        Weather Impact Assessment remains canonical for thresholds. This method
        only translates typed signals into operational recommendations. When
        weather is unavailable, returns an empty list (no fabricated output).

        Identity is stable per ``rule_code`` + weather scope key (not timestamp).
        """
        service = impact_service or WeatherImpactService(self._db)
        drafts: list[RecommendationDraft] = []

        for zone_id in self._weather_assessment_zone_ids():
            try:
                assessment = service.assess(zone_id=zone_id, hours=forecast_hours)
            except WeatherUnavailableError:
                continue

            for signal in assessment.signals:
                draft = self._weather_signal_to_draft(signal, assessment)
                if draft is not None:
                    drafts.append(draft)

        return drafts

    def _weather_assessment_zone_ids(self) -> list[str | None]:
        """Corridor first, then each registered zone (utility-neutral scope list)."""
        zone_ids = [zone.id for zone in ZoneRepository(self._db).list()]
        return [None, *zone_ids]

    @staticmethod
    def _weather_scope_key(zone_id: str | None) -> str:
        if zone_id:
            return f"weather-scope:zone:{zone_id}"
        return "weather-scope:corridor"

    def _weather_signal_to_draft(
        self,
        signal: WeatherImpactSignalOut,
        assessment: WeatherImpactAssessmentOut,
    ) -> RecommendationDraft | None:
        priority = self._weather_priority_from_severity(signal.severity)
        if priority is None:
            return None

        scope_key = self._weather_scope_key(signal.zone_id)
        rule_code = f"weather_{signal.signal_type.value}_{signal.observation_kind.value}"
        is_current = signal.observation_kind == WeatherImpactObservationKind.CURRENT
        timing = "Current" if is_current else "Forecast"
        scope_label = (
            f"zone {signal.zone_id}" if signal.zone_id else "corridor-wide"
        )
        signal_label = signal.signal_type.value.replace("_", " ")

        title = f"{timing} weather — {signal_label} ({scope_label})"

        description = (
            f"{timing} weather signal at {scope_label} centroid: "
            f"{signal.measured_value:.1f} {signal.unit} ({signal.severity} severity). "
            f"Rule: {signal.threshold_rule}. Provider: {signal.source}. "
            f"Observed at {signal.observed_at.isoformat()}. "
            "Weather is centroid-based, not per-asset."
        )

        recommendation = self._weather_recommendation_action(signal, timing, scope_label)
        static_note = self._weather_static_metadata_note(assessment.static_metadata_context)
        if static_note:
            description = f"{description} {static_note}"

        return RecommendationDraft(
            rule_code=rule_code,
            priority=priority,
            severity=signal.severity if signal.severity in {"critical", "high", "medium", "low"} else priority,
            title=title,
            description=description,
            affected_asset=scope_key,
            affected_zone=signal.zone_id or "",
            recommendation=recommendation,
            estimated_business_impact=(
                "Operational review recommended; weather evidence does not prove asset failure."
            ),
            confidence=0.92 if is_current else 0.82,
        )

    @staticmethod
    def _weather_priority_from_severity(severity: str) -> str | None:
        normalized = severity.lower()
        if normalized in {"critical", "high", "medium", "low"}:
            return normalized
        if normalized == "info":
            return "low"
        return None

    @staticmethod
    def _weather_recommendation_action(
        signal: WeatherImpactSignalOut,
        timing: str,
        scope_label: str,
    ) -> str:
        signal_label = signal.signal_type.value.replace("_", " ")
        if timing == "Forecast":
            return (
                f"Forecast indicates {signal.severity} {signal_label} conditions at the "
                f"{scope_label} weather centroid. Review operational readiness and monitor "
                f"assets in scope; weather alone does not confirm failure or flooding."
            )
        return (
            f"Current {signal.severity} {signal_label} conditions at the {scope_label} "
            f"weather centroid. Review operational readiness and monitor assets in scope; "
            f"weather alone does not confirm failure or flooding."
        )

    @staticmethod
    def _weather_static_metadata_note(
        static_context,
    ) -> str:
        if static_context is None:
            return ""
        high_flood = static_context.flood_risk.get("high", 0)
        if high_flood <= 0:
            return ""
        return (
            f"Static seed metadata: {high_flood} asset(s) in scope carry a high "
            f"flood_risk catalog label (not live flood probability)."
        )

    def environment_impact_rules(
        self,
        *,
        impact_service: EnvironmentImpactService | None = None,
        forecast_hours: int = 24,
    ) -> list[RecommendationDraft]:
        """
        Map deterministic EnvironmentImpactService signals to recommendation drafts.

        Environment Impact Assessment remains canonical for thresholds. This method
        only translates typed impact signals into operational recommendations.
        When environment data is unavailable, returns an empty list (no fabrication).

        Identity is stable per ``rule_code`` + environment scope key (not timestamp).
        """
        service = impact_service or EnvironmentImpactService(self._db)
        drafts: list[RecommendationDraft] = []

        for zone_id in self._environment_assessment_zone_ids():
            try:
                assessment = service.assess(zone_id=zone_id, hours=forecast_hours)
            except EnvironmentUnavailableError:
                continue

            if assessment.overall_severity == "none" or not assessment.signals:
                continue

            for signal in assessment.signals:
                draft = self._environment_signal_to_draft(signal, assessment)
                if draft is not None:
                    drafts.append(draft)

        return drafts

    def _environment_assessment_zone_ids(self) -> list[str | None]:
        """Corridor first, then each registered zone (utility-neutral scope list)."""
        zone_ids = [zone.id for zone in ZoneRepository(self._db).list()]
        return [None, *zone_ids]

    @staticmethod
    def _environment_scope_key(zone_id: str | None) -> str:
        if zone_id:
            return f"environment-scope:zone:{zone_id}"
        return "environment-scope:corridor"

    def _environment_signal_to_draft(
        self,
        signal: EnvironmentImpactSignalOut,
        assessment: EnvironmentImpactAssessmentOut,
    ) -> RecommendationDraft | None:
        priority = self._weather_priority_from_severity(signal.severity)
        if priority is None:
            return None

        signal_type = (
            signal.signal_type.value
            if hasattr(signal.signal_type, "value")
            else str(signal.signal_type)
        )
        observation_kind = (
            signal.observation_kind.value
            if hasattr(signal.observation_kind, "value")
            else str(signal.observation_kind)
        )
        scope_key = self._environment_scope_key(signal.zone_id)
        rule_code = f"environment_{signal_type}_{observation_kind}"
        is_current = signal.observation_kind == EnvironmentObservationKind.CURRENT
        timing = "Current" if is_current else "Forecast"
        scope_label = (
            f"zone {signal.zone_id}" if signal.zone_id else "corridor-wide"
        )
        signal_label = signal_type.replace("_", " ")

        title = f"{timing} environmental — {signal_label} ({scope_label})"

        aqi_note = ""
        if signal_type == "aqi":
            standard = str(signal.metadata.get("aqi_standard") or "").strip().lower()
            if standard != "us_aqi":
                # Impact service should already exclude unknown standards; belt-and-suspenders.
                return None
            aqi_note = (
                " AQI standard: us_aqi (United States Air Quality Index). "
                "Not CPCB AQI and not Indian National AQI."
            )

        description = (
            f"{timing} environmental condition at {scope_label} centroid: "
            f"{signal_label} {signal.measured_value:.1f} {signal.unit} "
            f"({signal.severity} severity; observation_kind={observation_kind}). "
            f"Rule: {signal.threshold_rule}. {signal.explanation} "
            f"Provider: {signal.source}. Observed at {signal.observed_at.isoformat()}."
            f"{aqi_note} "
            "Environmental evidence is centroid-based, not per-asset, and does not "
            "by itself prove asset failure or a regulatory violation."
        )
        if assessment.note and "stale" in assessment.note.lower():
            description = f"{description} Input note: {assessment.note}"

        recommendation = self._environment_recommendation_action(
            signal_type=signal_type,
            severity=signal.severity,
            timing=timing,
            scope_label=scope_label,
            is_aqi=(signal_type == "aqi"),
        )

        return RecommendationDraft(
            rule_code=rule_code,
            priority=priority,
            severity=(
                signal.severity
                if signal.severity in {"critical", "high", "medium", "low"}
                else priority
            ),
            title=title,
            description=description,
            affected_asset=scope_key,
            affected_zone=signal.zone_id or "",
            recommendation=recommendation,
            estimated_business_impact=(
                "Operational review recommended; environmental evidence does not "
                "prove asset failure or regulatory non-compliance."
            ),
            confidence=0.90 if is_current else 0.80,
        )

    @staticmethod
    def _environment_recommendation_action(
        *,
        signal_type: str,
        severity: str,
        timing: str,
        scope_label: str,
        is_aqi: bool,
    ) -> str:
        signal_label = signal_type.replace("_", " ")
        aqi_clause = (
            " Treat the index as US AQI (us_aqi), not CPCB/Indian National AQI."
            if is_aqi
            else ""
        )
        if timing == "Forecast":
            return (
                f"Forecast environmental condition indicates {severity} {signal_label} "
                f"at the {scope_label} environment centroid. Review outdoor/crew exposure "
                f"and operational readiness for assets in scope; do not treat this as a "
                f"confirmed asset failure or regulatory violation.{aqi_clause}"
            )
        return (
            f"Current environmental condition shows {severity} {signal_label} at the "
            f"{scope_label} environment centroid. Review outdoor/crew exposure and "
            f"operational readiness for assets in scope; do not treat this as a "
            f"confirmed asset failure or regulatory violation.{aqi_clause}"
        )

    def resilience_rules(
        self,
        *,
        resilience_service: ResilienceAssessmentService | None = None,
        forecast_hours: int = 24,
    ) -> list[RecommendationDraft]:
        """
        Map ResilienceAssessmentService posture (≥ high) to at most one draft per scope.

        Does not duplicate per-signal weather/environment recommendations.
        Identity is stable per ``resilience_posture`` + resilience scope key.
        """
        service = resilience_service or ResilienceAssessmentService(self._db)
        ctx = service.get_context(hours=forecast_hours)
        drafts: list[RecommendationDraft] = []

        for assessment in (ctx.corridor, *ctx.zones):
            draft = self._resilience_assessment_to_draft(assessment)
            if draft is not None:
                drafts.append(draft)
        return drafts

    # Phase 16.2 — critical/unavailable profile conditions that may warrant
    # cross-utility dependency recommendations. Degraded/at_risk are intentionally
    # excluded (no matching OI convention for dependency recs in this phase).
    _CROSS_UTILITY_TRIGGERS = frozenset({"critical", "unavailable"})

    def cross_utility_dependency_rules(self) -> list[RecommendationDraft]:
        """
        Consume CrossUtilityImpactService for sources in critical/unavailable
        condition that have explicit outgoing supplies dependents.

        One coherent draft per source asset (multi-utility dependents listed together).
        Does not traverse the dependency graph itself — impact service is the SoT.
        Does not create work orders, alerts, or incidents.
        """
        from app.domain.enums import DependencyType
        from app.domain.repositories import AssetDependencyRepository
        from app.services.cross_utility_impact_service import CrossUtilityImpactService

        impact_svc = CrossUtilityImpactService(self._db)
        edges = AssetDependencyRepository(self._db).list(
            dependency_type=DependencyType.SUPPLIES.value,
            enabled=True,
        )
        source_ids = sorted({e.source_asset_id for e in edges})
        drafts: list[RecommendationDraft] = []
        for source_id in source_ids:
            asset = self._assets.get(source_id)
            if asset is None:
                continue
            trigger = self._resolve_cross_utility_trigger(asset)
            if trigger is None:
                continue
            assessment = impact_svc.assess_asset_impact(
                asset_id=source_id,
                condition=trigger,
            )
            if int(assessment.get("affected_asset_count") or 0) <= 0:
                continue
            draft = self._cross_utility_assessment_to_draft(asset=asset, assessment=assessment)
            if draft is not None:
                drafts.append(draft)
        return drafts

    def _resolve_cross_utility_trigger(self, asset: Any) -> str | None:
        """Map existing asset condition/status onto Phase 16.1 impact triggers."""
        profile = dict(asset.profile or {}) if isinstance(asset.profile, dict) else {}
        raw = str(profile.get("condition") or "").strip().lower()
        raw = raw.replace("-", "_").replace(" ", "_")
        if raw in {"offline", "failed"}:
            raw = "unavailable"
        if raw in self._CROSS_UTILITY_TRIGGERS:
            return raw

        status = str(getattr(asset, "status", "") or "").strip().lower()
        if status in {"offline", "unavailable", "failed", "out_of_service"}:
            return "unavailable"
        return None

    def _cross_utility_assessment_to_draft(
        self,
        *,
        asset: Any,
        assessment: dict[str, Any],
    ) -> RecommendationDraft | None:
        affected = list(assessment.get("affected_assets") or [])
        if not affected:
            return None

        trigger = str(assessment.get("trigger_condition") or "critical")
        source_id = str(assessment.get("source_asset_id") or asset.id)
        source_utility = str(assessment.get("source_utility") or asset.utility_kind)
        source_zone = str(assessment.get("source_zone") or asset.zone_id)
        source_name = str(assessment.get("source_asset_name") or asset.name)
        utilities = [str(u) for u in (assessment.get("affected_utilities") or [])]
        zones = [str(z) for z in (assessment.get("affected_zones") or [])]

        util_label = self._utility_display(source_utility)
        zone_label = self._zone_display(source_zone)
        affected_phrases = [
            (
                f"{self._utility_display(str(row.get('utility') or ''))} "
                f"{str(row.get('asset_name') or row.get('asset_id') or '')} "
                f"({row.get('asset_id')})"
            ).strip()
            for row in affected
        ]
        affected_list = self._join_phrases(affected_phrases)
        affected_util_txt = self._join_phrases(
            [self._utility_display(u) for u in utilities]
        ) or "dependent utilities"
        affected_zone_txt = self._join_phrases(
            [self._zone_display(z) for z in zones]
        ) or zone_label

        title = f"Cross-utility dependency impact — {source_id}"
        description = (
            f"{util_label} asset {source_name} ({source_id}) in {zone_label} is "
            f"{trigger}. Explicit supplies dependencies identify potential impact on "
            f"{affected_list} "
            f"(utilities: {affected_util_txt}; zones: {affected_zone_txt}). "
            "This is potential dependency impact only — not a declared multi-utility outage."
        )
        recommendation = (
            f"{util_label} {source_id} is {trigger} and may affect dependent "
            f"{affected_list} in {affected_zone_txt}. Verify source asset availability "
            "and dependent asset operating conditions before maintenance activity."
        )
        evidence = str(assessment.get("explanation") or "").strip()
        if evidence:
            description = f"{description} Evidence: {evidence}"

        # Structured context for Work Order recommendation_snapshot (Phase 16.4).
        # Reuses CrossUtilityImpactService assessment — no second traversal.
        impact_assets = [
            {
                "asset_id": str(row.get("asset_id") or ""),
                "asset_name": str(row.get("asset_name") or ""),
                "utility": str(row.get("utility") or ""),
                "zone": str(row.get("zone") or ""),
                "impact_kind": str(row.get("impact_kind") or ""),
                "depth": int(row.get("depth") or 0),
                "posture": str(row.get("posture") or "potentially_affected"),
            }
            for row in affected
        ]
        dependency_paths = [
            {
                "asset_id": str(p.get("asset_id") or ""),
                "depth": int(p.get("depth") or 0),
                "impact_kind": str(p.get("impact_kind") or ""),
                "path_asset_ids": list(p.get("path_asset_ids") or []),
                "path_dependency_ids": list(p.get("path_dependency_ids") or []),
            }
            for p in (assessment.get("dependency_paths") or [])
        ]
        context = {
            "kind": "cross_utility_dependency_impact",
            "source_asset": {
                "asset_id": source_id,
                "utility": source_utility,
                "zone_id": source_zone,
                "condition": trigger,
            },
            "impact": {
                "affected_assets": impact_assets,
                "affected_utilities": utilities,
                "affected_zones": zones,
                "dependency_paths": dependency_paths,
                "hypothetical": True,
                "assessment_kind": str(
                    assessment.get("assessment_kind") or "explicit_dependency_impact"
                ),
            },
        }

        return RecommendationDraft(
            rule_code="cross_utility_dependency_impact",
            priority="critical",
            severity="critical",
            title=title,
            description=description,
            # Stable ID key: source asset (one rec per dependency event).
            affected_asset=source_id,
            affected_zone=source_zone,
            recommendation=recommendation,
            estimated_business_impact=(
                "Potential cross-utility operational impact via explicit dependency; "
                "verify dependent assets — does not prove outage or quantify financial exposure."
            ),
            confidence=0.86 if trigger == "unavailable" else 0.84,
            context=context,
        )

    @staticmethod
    def _utility_display(utility: str) -> str:
        raw = (utility or "").strip().lower()
        return {
            "power": "Power",
            "water": "Water",
            "gas": "Gas",
            "communications": "Communications",
        }.get(raw, raw.capitalize() if raw else "Utility")

    @staticmethod
    def _zone_display(zone_id: str) -> str:
        raw = (zone_id or "").strip()
        if not raw:
            return "unknown zone"
        # zone-c → Zone C
        if raw.lower().startswith("zone-") and len(raw) > 5:
            return f"Zone {raw.split('-', 1)[1].upper()}"
        if raw.lower().startswith("zone") and len(raw) > 4:
            return f"Zone {raw[4:].upper()}"
        return raw

    @staticmethod
    def _join_phrases(parts: list[str]) -> str:
        clean = [p.strip() for p in parts if p and str(p).strip()]
        if not clean:
            return ""
        if len(clean) == 1:
            return clean[0]
        if len(clean) == 2:
            return f"{clean[0]} and {clean[1]}"
        return f"{', '.join(clean[:-1])}, and {clean[-1]}"

    def _resilience_assessment_to_draft(
        self,
        assessment: ResilienceAssessmentOut,
    ) -> RecommendationDraft | None:
        posture = (assessment.overall_posture or "none").lower()
        if posture not in {"high", "critical"}:
            return None

        zone_id = assessment.zone_id
        scope_key = (
            f"resilience-scope:zone:{zone_id}" if zone_id else "resilience-scope:corridor"
        )
        scope_label = f"zone {zone_id}" if zone_id else "corridor"
        rule_code = "resilience_posture"

        driver_bits = []
        for d in assessment.drivers:
            src = d.source.value if hasattr(d.source, "value") else str(d.source)
            driver_bits.append(f"{src}={d.severity}")
        driver_summary = ", ".join(driver_bits) if driver_bits else "no drivers"

        asset_ev = next(
            (
                d.evidence
                for d in assessment.drivers
                if (d.source.value if hasattr(d.source, "value") else str(d.source))
                == "asset_posture"
            ),
            {},
        )
        stale_note = " Inputs include stale weather/environment data." if assessment.inputs_stale else ""
        unavailable = []
        if not assessment.inputs_available.weather_available:
            unavailable.append("weather")
        if not assessment.inputs_available.environment_available:
            unavailable.append("environment")
        unavail_note = (
            f" Unavailable inputs: {', '.join(unavailable)}." if unavailable else ""
        )

        title = f"Resilience posture {posture} ({scope_label})"
        description = (
            f"{scope_label.capitalize()} resilience posture is {posture} "
            f"(POC operational posture model, not a 0–100 index). "
            f"Drivers: {driver_summary}. "
            f"Static asset evidence: {asset_ev.get('high_criticality', 0)} high/very-high "
            f"criticality; {asset_ev.get('high_criticality_no_backup', 0)} with backup=none "
            f"of {asset_ev.get('asset_count', 0)} assets. "
            f"Assessed at {assessment.assessed_at.isoformat()}."
            f"{stale_note}{unavail_note} "
            "This synthesizes existing impact/scenario/asset posture and does not "
            "replace weather or environment recommendations."
        )
        recommendation = (
            f"{scope_label.capitalize()} resilience posture is {posture} due to combined "
            "operational and/or environmental stress. Review readiness across the scope, "
            "prioritize critical assets without backup, and use weather/environment "
            "recommendations for signal-level detail. Do not treat this as a confirmed "
            "asset failure or as automatic work-order grounds."
        )

        return RecommendationDraft(
            rule_code=rule_code,
            priority=posture,
            severity=posture,
            title=title,
            description=description,
            affected_asset=scope_key,
            affected_zone=zone_id or "",
            recommendation=recommendation,
            estimated_business_impact=(
                "Operational readiness review recommended; resilience posture does not "
                "prove asset failure or quantify financial exposure."
            ),
            confidence=0.88 if not assessment.inputs_stale else 0.75,
        )

    @staticmethod
    def _priority_rank(priority: str) -> int:
        return {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(priority, 0)

    @staticmethod
    def stable_id(rule_code: str, asset_id: str) -> str:
        digest = sha1(f"{rule_code}:{asset_id}".encode()).hexdigest()[:12]
        return f"rec-{digest}"


def level_fallback(metrics: dict[str, float]) -> float:
    return float(metrics.get("tank_level") or metrics.get("reservoir_level") or 0.0)
