"""Deterministic operational intelligence — rule-based recommendations (no LLM)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha1
from typing import Any

from sqlalchemy.orm import Session

from app.domain.repositories import AssetRepository, TelemetryRepository
from app.scenarios.definitions import ScenarioDefinition, ScenarioKey, get_scenario
from app.utilities.water import health as water_health


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


class OperationalIntelligenceService:
    """
    Analyses live telemetry and active scenario to produce engineering
    recommendations using threshold and scenario-aware rules.
    """

    HIGH_PRIORITIES = frozenset({"critical", "high"})

    def __init__(self, db: Session) -> None:
        self._assets = AssetRepository(db)
        self._telemetry = TelemetryRepository(db)

    def generate(
        self,
        *,
        scenario_key: str,
        scenario_intensity: float,
        now: datetime | None = None,
    ) -> list[RecommendationDraft]:
        now = now or datetime.now(timezone.utc)
        try:
            scenario = get_scenario(scenario_key)
        except KeyError:
            scenario = get_scenario(ScenarioKey.NORMAL.value)

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
            drafts.extend(
                self._scenario_rules(
                    scenario=scenario,
                    intensity=scenario_intensity,
                    asset_id=asset.id,
                    asset_name=asset.name,
                    zone_id=asset.zone_id,
                    asset_type=asset.type,
                    metrics=metrics,
                    health=health,
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
                    confidence=float(prediction.get("confidence", 0.5)),
                )
            )
        return drafts

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

    @staticmethod
    def _priority_rank(priority: str) -> int:
        return {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(priority, 0)

    @staticmethod
    def stable_id(rule_code: str, asset_id: str) -> str:
        digest = sha1(f"{rule_code}:{asset_id}".encode()).hexdigest()[:12]
        return f"rec-{digest}"


def level_fallback(metrics: dict[str, float]) -> float:
    return float(metrics.get("tank_level") or metrics.get("reservoir_level") or 0.0)
