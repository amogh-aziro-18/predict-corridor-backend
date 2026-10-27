"""Isolated operational what-if simulation (not a hydraulic physics solver).

Applies existing scenario metric rules in-memory to a snapshot of live
telemetry. Never writes PostgreSQL telemetry, never changes simulator state,
never persists scenario selection.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationAppError
from app.domain.repositories import AssetRepository
from app.domain.repositories.telemetry_repository import TelemetryRepository
from app.scenarios.definitions import ScenarioKey, get_scenario
from app.scenarios.modifiers import apply_rule, rules_for
from app.services.asset_dependency_service import AssetDependencyService
from app.services.cross_utility_impact_service import CrossUtilityImpactService
from app.services.parameter_simulation import (
    list_supported_parameters,
    nominal_from_profile,
    project_parameter,
)

SUPPORTED_HORIZONS = (6, 12, 24, 48)

# Operator-facing mitigations keyed by scenario — qualitative guidance only.
MITIGATIONS: dict[ScenarioKey, list[str]] = {
    ScenarioKey.NORMAL: ["Maintain normal monitoring cadence."],
    ScenarioKey.PEAK_DEMAND: [
        "Stagger non-critical industrial demand windows.",
        "Pre-position standby pumping capacity.",
    ],
    ScenarioKey.PUMP_FAILURE: [
        "Activate standby pump where available.",
        "Isolate failed unit and verify valve positions.",
        "Protect critical downstream supply zones.",
    ],
    ScenarioKey.PIPELINE_LEAK: [
        "Isolate leaking segment if operable valves allow.",
        "Increase upstream monitoring of pressure and outflow.",
    ],
    ScenarioKey.RESERVOIR_LOW: [
        "Increase source inflow windows.",
        "Curtail non-essential demand until storage recovers.",
    ],
    ScenarioKey.CONTAMINATION: [
        "Hold treated-water release pending quality confirmation.",
        "Increase chlorine residual checks at plant outlet.",
    ],
    ScenarioKey.HEAVY_RAIN: [
        "Prepare for elevated turbidity and inflow surge.",
        "Monitor reservoir freeboard and treatment throughput.",
    ],
    ScenarioKey.POWER_OUTAGE: [
        "Engage backup power for critical pumps.",
        "Prioritize critical-zone supply until grid restores.",
    ],
}


class OperationalSimulationService:
    """Read-only snapshot + in-memory scenario projection."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._assets = AssetRepository(db)
        self._telemetry = TelemetryRepository(db)

    def run_what_if(
        self,
        *,
        asset_id: str,
        scenario_key: str,
        horizon_hours: int = 24,
    ) -> dict[str, Any]:
        if horizon_hours not in SUPPORTED_HORIZONS:
            raise ValidationAppError(
                f"Unsupported horizon {horizon_hours}. "
                f"Supported: {', '.join(str(h) for h in SUPPORTED_HORIZONS)}."
            )

        try:
            scenario = get_scenario(scenario_key)
            key = ScenarioKey(scenario_key)
        except KeyError as exc:
            raise ValidationAppError(f"Unsupported scenario: {scenario_key}") from exc

        asset = self._assets.get(asset_id)
        if asset is None:
            raise NotFoundError(f"Asset not found: {asset_id}")

        latest_index = self._latest_index(utility_kind=asset.utility_kind)
        baseline_metrics = self._metrics_for_asset(latest_index, asset_id)
        intensity = self._intensity_for_horizon(horizon_hours)
        steps = max(1, horizon_hours // 6)

        predicted_metrics, metric_deltas = self._project_metrics(
            asset_type=asset.type,
            baseline=baseline_metrics,
            scenario_key=key,
            intensity=intensity,
            steps=steps,
        )

        zone_assets = self._assets.list(zone_id=asset.zone_id)
        affected = self._affected_assets(
            zone_assets=zone_assets,
            latest_index=latest_index,
            scenario_key=key,
            primary_id=asset.id,
            intensity=intensity,
            steps=steps,
        )

        risk = self._operational_risk(key, metric_deltas, predicted_metrics)
        service_impact = self._service_impact(key, risk, asset.zone_id)

        return {
            "simulation_id": f"sim-{uuid4().hex[:12]}",
            "mode": "operational_scenario",
            "physics_based": False,
            "disclaimer": (
                "Operational scenario simulation using deterministic metric rules. "
                "Not a hydraulic / power-flow physics solver. "
                "Live telemetry and simulator state are not modified."
            ),
            "generated_at": datetime.now(timezone.utc),
            "horizon_hours": horizon_hours,
            "intensity": intensity,
            "status": "completed",
            "target_asset": {
                "asset_id": asset.id,
                "asset_name": asset.name,
                "utility": asset.utility_kind,
                "asset_type": asset.type,
                "zone": asset.zone_id,
                "status": asset.status,
                "health": asset.health,
                "criticality": asset.criticality,
            },
            "scenario": {
                "key": scenario.key.value,
                "name": scenario.name,
                "description": scenario.description,
                "category": scenario.category,
                "default_severity": scenario.default_severity,
            },
            "baseline": {
                "health": asset.health,
                "status": asset.status,
                "metrics": baseline_metrics,
            },
            "predicted": {
                "metrics": predicted_metrics,
                "metric_deltas": metric_deltas,
            },
            "affected_assets": affected,
            "affected_zones": [asset.zone_id],
            "operational_risk": risk,
            "service_impact": service_impact,
            "mitigations": list(MITIGATIONS.get(key, [])),
            "dependencies": AssetDependencyService(self._db).simulation_dependencies_payload(
                asset.id
            ),
        }

    def baseline_for_asset(self, asset_id: str) -> dict[str, Any]:
        asset = self._assets.get(asset_id)
        if asset is None:
            raise NotFoundError(f"Asset not found: {asset_id}")
        latest_index = self._latest_index(utility_kind=asset.utility_kind)
        return {
            "asset_id": asset.id,
            "asset_name": asset.name,
            "utility": asset.utility_kind,
            "asset_type": asset.type,
            "zone": asset.zone_id,
            "status": asset.status,
            "health": asset.health,
            "criticality": asset.criticality,
            "risk": asset.risk,
            "predicted_failure_days": asset.predicted_failure_days,
            "metrics": self._metrics_for_asset(latest_index, asset_id),
            "captured_at": datetime.now(timezone.utc),
        }

    def parameters_for_asset(self, asset_id: str) -> dict[str, Any]:
        """List explainable parameter what-if options for an asset."""
        baseline = self.baseline_for_asset(asset_id)
        metric_keys = {
            str(m["metric_key"])
            for m in baseline["metrics"]
            if m.get("metric_key") is not None
        }
        return {
            "asset_id": baseline["asset_id"],
            "utility": baseline["utility"],
            "asset_type": baseline["asset_type"],
            "parameters": list_supported_parameters(baseline["utility"], metric_keys),
            "disclaimer": (
                "Parameter what-if uses documented Water/Power/Gas/Communications threshold bands. "
                "Not a physics-based digital twin. Unsupported parameters are marked unavailable."
            ),
        }

    def run_parameter_what_if(
        self,
        *,
        asset_id: str,
        parameter: str,
        delta_percent: float | None = None,
        absolute_value: float | None = None,
    ) -> dict[str, Any]:
        """
        Deterministic parameter projection from live baseline + threshold bands.

        Never writes telemetry, never assigns technicians, never creates work orders.
        """
        if delta_percent is None and absolute_value is None:
            raise ValidationAppError("Provide delta_percent or absolute_value.")
        if delta_percent is not None and absolute_value is not None:
            raise ValidationAppError("Provide only one of delta_percent or absolute_value.")

        asset = self._assets.get(asset_id)
        if asset is None:
            raise NotFoundError(f"Asset not found: {asset_id}")

        baseline = self.baseline_for_asset(asset_id)
        metrics = {str(m["metric_key"]): m for m in baseline["metrics"]}
        metric = metrics.get(parameter)
        baseline_value = float(metric["value"]) if metric and metric.get("value") is not None else None
        profile = asset.profile if isinstance(asset.profile, dict) else {}
        utility_kind = str(baseline["utility"]).lower()
        nominal_value = (
            nominal_from_profile(parameter, profile)
            if utility_kind in {"gas", "communications"}
            else None
        )

        projection = project_parameter(
            utility_kind=str(baseline["utility"]),
            asset_type=str(baseline["asset_type"]),
            parameter=parameter,
            baseline_value=baseline_value,
            delta_percent=delta_percent,
            absolute_value=absolute_value,
            nominal_value=nominal_value,
        )

        return {
            "simulation_id": f"param-{uuid4().hex[:12]}",
            "mode": "parameter_what_if",
            "physics_based": False,
            "disclaimer": (
                "Operational parameter scenario using documented Water/Power/Gas/"
                "Communications threshold bands. Not a hydraulic / power-flow / gas-network / "
                "telecom-physics solver. Live telemetry and simulator state are not modified."
            ),
            "generated_at": datetime.now(timezone.utc),
            "status": "completed" if projection.available else "unavailable",
            "target_asset": {
                "asset_id": baseline["asset_id"],
                "asset_name": baseline["asset_name"],
                "utility": baseline["utility"],
                "asset_type": baseline["asset_type"],
                "zone": baseline["zone"],
                "status": baseline["status"],
                "health": baseline["health"],
                "criticality": baseline["criticality"],
                "risk": baseline.get("risk"),
                "predicted_failure_days": baseline.get("predicted_failure_days"),
            },
            "parameter": projection.parameter,
            "available": projection.available,
            "baseline_value": projection.baseline_value,
            "projected_value": projection.projected_value,
            "unit": projection.unit,
            "delta_percent": delta_percent,
            "absolute_value": absolute_value,
            "projected_condition": projection.condition,
            "potential_alert": projection.potential_alert,
            "potential_risk": projection.potential_risk,
            "explanation": projection.explanation,
            "recommended_action": projection.recommended_action,
            "rule_refs": projection.rule_refs,
            "dependencies": AssetDependencyService(self._db).simulation_dependencies_payload(
                str(baseline["asset_id"])
            ),
            "dependency_impact": self._optional_dependency_impact(
                asset_id=str(baseline["asset_id"]),
                projected_condition=projection.condition,
            ),
        }

    def _optional_dependency_impact(
        self, *, asset_id: str, projected_condition: str
    ) -> dict[str, Any] | None:
        """
        Attach structured Phase 16.3 dependency impact when projection is CRITICAL.

        Uses CrossUtilityImpactService as the single source of truth (no second BFS).
        Non-CRITICAL projections return None (impact not claimed). CRITICAL with no
        dependents returns an honest empty assessed payload. Hypothetical only —
        does not mutate state or generate recommendations.
        """
        if (projected_condition or "").strip().upper() != "CRITICAL":
            return None

        asset = self._assets.get(asset_id)
        if asset is None:
            return None

        assessment = CrossUtilityImpactService(self._db).assess_asset_impact(
            asset_id=asset_id,
            condition="critical",
        )
        direct = [
            self._dependency_impact_asset_ref(row)
            for row in (assessment.get("direct_impacts") or [])
        ]
        propagated = [
            self._dependency_impact_asset_ref(row)
            for row in (assessment.get("propagated_impacts") or [])
        ]
        affected = [*direct, *propagated]
        paths = [
            {
                "asset_id": str(p.get("asset_id") or ""),
                "depth": int(p.get("depth") or 0),
                "impact_kind": str(p.get("impact_kind") or ""),
                "path_asset_ids": list(p.get("path_asset_ids") or []),
                "path_dependency_ids": list(p.get("path_dependency_ids") or []),
            }
            for p in (assessment.get("dependency_paths") or [])
        ]

        return {
            "assessed": True,
            "hypothetical": True,
            "mutates_operational_state": False,
            "trigger_condition": "critical",
            "projected_condition": "CRITICAL",
            "source_asset_id": str(assessment.get("source_asset_id") or asset.id),
            "source_asset_name": str(
                assessment.get("source_asset_name") or asset.name
            ),
            "source_utility": str(
                assessment.get("source_utility") or asset.utility_kind
            ),
            "source_zone": str(assessment.get("source_zone") or asset.zone_id),
            "direct_impacts": direct,
            "propagated_impacts": propagated,
            "affected_assets": affected,
            "affected_asset_count": int(assessment.get("affected_asset_count") or 0),
            "affected_utilities": list(assessment.get("affected_utilities") or []),
            "affected_zones": list(assessment.get("affected_zones") or []),
            "direct_impact_asset_ids": [row["asset_id"] for row in direct],
            "dependency_paths": paths,
            "explanation": self._digital_twin_dependency_explanation(
                source_id=str(assessment.get("source_asset_id") or asset.id),
                affected=affected,
            ),
            "note": (
                "Hypothetical potential dependency impact only — not a declared "
                "multi-utility outage. No telemetry, alerts, work orders, or "
                "recommendations were modified."
            ),
        }

    @staticmethod
    def _dependency_impact_asset_ref(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "asset_id": str(row.get("asset_id") or ""),
            "asset_name": str(row.get("asset_name") or ""),
            "utility": str(row.get("utility") or ""),
            "zone": str(row.get("zone") or ""),
            "impact_kind": str(row.get("impact_kind") or "direct"),
            "depth": int(row.get("depth") or 0),
            "posture": str(row.get("posture") or "potentially_affected"),
        }

    @staticmethod
    def _digital_twin_dependency_explanation(
        *, source_id: str, affected: list[dict[str, Any]]
    ) -> str:
        if not affected:
            return (
                f"No potential dependency impact identified for {source_id} — "
                "no explicit outgoing supplies dependents."
            )
        phrases: list[str] = []
        for row in affected:
            util = str(row.get("utility") or "utility").capitalize()
            aid = str(row.get("asset_id") or "")
            zone = str(row.get("zone") or "")
            zone_txt = f" / {zone}" if zone else ""
            phrases.append(f"{util} asset {aid}{zone_txt}")
        if len(phrases) == 1:
            joined = phrases[0]
        elif len(phrases) == 2:
            joined = f"{phrases[0]} and {phrases[1]}"
        else:
            joined = f"{', '.join(phrases[:-1])}, and {phrases[-1]}"
        return (
            f"Potential dependency impact identified for dependent {joined}. "
            "Hypothetical Digital Twin projection only — does not declare asset "
            "or zone failure."
        )

    def _latest_index(self, *, utility_kind: str) -> dict[str, dict[str, dict[str, Any]]]:
        """asset_id → metric_key → metric snapshot (from DISTINCT ON latest)."""
        index: dict[str, dict[str, dict[str, Any]]] = {}
        for asset_id, _name, tel in self._telemetry.latest_rows(utility_kind=utility_kind):
            metrics = index.setdefault(asset_id, {})
            metrics[str(tel.metric_key)] = {
                "metric_key": str(tel.metric_key),
                "value": float(tel.value),
                "unit": str(tel.unit or ""),
                "recorded_at": tel.recorded_at,
            }
        return index

    @staticmethod
    def _metrics_for_asset(
        index: dict[str, dict[str, dict[str, Any]]],
        asset_id: str,
    ) -> list[dict[str, Any]]:
        metrics = index.get(asset_id, {})
        return sorted(metrics.values(), key=lambda m: m["metric_key"])

    @staticmethod
    def _intensity_for_horizon(horizon_hours: int) -> float:
        return round(min(1.0, 0.35 + horizon_hours / 80.0), 3)

    @staticmethod
    def _project_metrics(
        *,
        asset_type: str,
        baseline: list[dict[str, Any]],
        scenario_key: ScenarioKey,
        intensity: float,
        steps: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        rules = rules_for(scenario_key, asset_type)
        predicted: list[dict[str, Any]] = []
        deltas: list[dict[str, Any]] = []

        for metric in baseline:
            key = metric["metric_key"]
            base = float(metric["value"])
            rule = rules.get(key)
            value = base
            if rule is not None and scenario_key != ScenarioKey.NORMAL:
                for _ in range(steps):
                    value = apply_rule(value, rule, intensity=intensity)
            value = round(float(value), 2)
            predicted.append(
                {
                    "metric_key": key,
                    "value": value,
                    "unit": metric.get("unit") or "",
                }
            )
            direction = "stable"
            if rule is not None and abs(value - base) > 1e-6:
                direction = "decrease" if value < base else "increase"
            deltas.append(
                {
                    "metric_key": key,
                    "from_value": round(base, 2),
                    "to_value": value,
                    "direction": direction,
                    "absolute_change": round(value - base, 2),
                    "percent_change": (
                        round(((value - base) / base) * 100.0, 1) if abs(base) > 1e-9 else None
                    ),
                }
            )

        # Include scenario metrics missing from baseline (qualitative only skipped).
        for key, rule in rules.items():
            if any(m["metric_key"] == key for m in predicted):
                continue
            # No baseline observation — skip inventing a starting value.
            deltas.append(
                {
                    "metric_key": key,
                    "from_value": None,
                    "to_value": None,
                    "direction": "unavailable",
                    "absolute_change": None,
                    "percent_change": None,
                    "note": "No live baseline telemetry for this metric",
                }
            )

        return predicted, deltas

    def _affected_assets(
        self,
        *,
        zone_assets: list[Any],
        latest_index: dict[str, dict[str, dict[str, Any]]],
        scenario_key: ScenarioKey,
        primary_id: str,
        intensity: float,
        steps: int,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for asset in zone_assets:
            rules = rules_for(scenario_key, asset.type)
            if not rules and asset.id != primary_id:
                continue
            metrics = self._metrics_for_asset(latest_index, asset.id)
            _, deltas = self._project_metrics(
                asset_type=asset.type,
                baseline=metrics,
                scenario_key=scenario_key,
                intensity=intensity,
                steps=steps,
            )
            changing = [d for d in deltas if d.get("direction") in {"increase", "decrease"}]
            if asset.id != primary_id and not changing:
                continue
            out.append(
                {
                    "asset_id": asset.id,
                    "asset_name": asset.name,
                    "asset_type": asset.type,
                    "zone": asset.zone_id,
                    "is_primary": asset.id == primary_id,
                    "impacted_metrics": [
                        {
                            "metric_key": d["metric_key"],
                            "direction": d["direction"],
                            "percent_change": d.get("percent_change"),
                        }
                        for d in changing
                    ],
                }
            )
        return out

    @staticmethod
    def _operational_risk(
        key: ScenarioKey,
        deltas: list[dict[str, Any]],
        predicted: list[dict[str, Any]],
    ) -> str:
        if key == ScenarioKey.NORMAL:
            return "low"
        severity = get_scenario(key.value).default_severity
        # Escalate if critical hydraulics collapse in projection.
        by_key = {m["metric_key"]: m["value"] for m in predicted}
        if by_key.get("pressure") is not None and by_key["pressure"] < 1.2:
            return "critical"
        if by_key.get("pump_running") is not None and by_key["pump_running"] < 0.5:
            return "critical" if key == ScenarioKey.POWER_OUTAGE else "high"
        strong = [
            d
            for d in deltas
            if d.get("percent_change") is not None and abs(float(d["percent_change"])) >= 25
        ]
        if strong and severity in {"medium", "low"}:
            return "high"
        return severity

    @staticmethod
    def _service_impact(key: ScenarioKey, risk: str, zone_id: str) -> str:
        if key == ScenarioKey.NORMAL:
            return f"No material service disruption projected for {zone_id}."
        if risk == "critical":
            return f"Potential critical supply disruption in {zone_id}."
        if risk == "high":
            return f"Elevated risk of service degradation in {zone_id}."
        if risk == "medium":
            return f"Moderate operational stress projected for {zone_id}."
        return f"Limited operational impact projected for {zone_id}."
