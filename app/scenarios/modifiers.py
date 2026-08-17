"""Per-scenario metric adjustment rules — gradual drift toward stressed targets."""

from __future__ import annotations

from dataclasses import dataclass

from app.scenarios.definitions import ScenarioKey


@dataclass(frozen=True)
class MetricRule:
    """Gradual per-tick adjustment scaled by scenario intensity (0–1)."""

    drift: float = 0.0
    target: float | None = None
    pull_rate: float = 0.04
    floor: float | None = None
    ceiling: float | None = None


# asset_type → metric_key → rule
ScenarioRules = dict[str, dict[str, MetricRule]]

SCENARIO_RULES: dict[ScenarioKey, ScenarioRules] = {
    ScenarioKey.NORMAL: {},
    ScenarioKey.PEAK_DEMAND: {
        "pump_station": {
            "flow_rate": MetricRule(drift=10.0, ceiling=2200.0),
            "pump_speed": MetricRule(drift=6.0, ceiling=1700.0),
            "pressure": MetricRule(drift=-0.04, floor=2.0),
        },
        "distribution_network": {
            "flow_rate": MetricRule(drift=8.0, ceiling=2000.0),
            "pressure": MetricRule(drift=-0.03, floor=2.2),
        },
        "flow_meter": {
            "flow_rate": MetricRule(drift=12.0, ceiling=2300.0),
        },
    },
    ScenarioKey.PUMP_FAILURE: {
        "pump_station": {
            "pressure": MetricRule(drift=-0.07, floor=0.6),
            "flow_rate": MetricRule(drift=-10.0, floor=15.0),
            "pump_speed": MetricRule(drift=-12.0, floor=0.0, target=0.0, pull_rate=0.06),
            "pump_running": MetricRule(target=0.0, pull_rate=0.08),
        },
        "distribution_network": {
            "pressure": MetricRule(drift=-0.05, floor=1.0),
            "flow_rate": MetricRule(drift=-8.0, floor=30.0),
        },
        "pressure_monitoring_station": {
            "pressure": MetricRule(drift=-0.06, floor=0.8),
        },
    },
    ScenarioKey.PIPELINE_LEAK: {
        "reservoir": {
            "outflow": MetricRule(drift=14.0, ceiling=900.0),
            "inflow": MetricRule(drift=-4.0, floor=200.0),
        },
        "distribution_network": {
            "pressure": MetricRule(drift=-0.06, floor=1.2),
            "flow_rate": MetricRule(drift=-9.0, floor=40.0),
        },
        "valve_network": {
            "pressure": MetricRule(drift=-0.04, floor=1.5),
        },
        "flow_meter": {
            "flow_rate": MetricRule(drift=-7.0, floor=50.0),
        },
    },
    ScenarioKey.RESERVOIR_LOW: {
        "storage_tank": {
            "tank_level": MetricRule(drift=-0.45, floor=12.0),
            "pressure": MetricRule(drift=-0.03, floor=1.6),
        },
        "reservoir": {
            "reservoir_level": MetricRule(drift=-0.35, floor=15.0),
            "outflow": MetricRule(drift=5.0, ceiling=650.0),
        },
        "pump_station": {
            "flow_rate": MetricRule(drift=-4.0, floor=120.0),
        },
    },
    ScenarioKey.CONTAMINATION: {
        "water_treatment_plant": {
            "turbidity": MetricRule(drift=0.12, ceiling=8.5),
            "chlorine_level": MetricRule(drift=-0.015, floor=0.08),
            "flow_rate": MetricRule(drift=-3.0, floor=200.0),
        },
    },
    ScenarioKey.HEAVY_RAIN: {
        "reservoir": {
            "inflow": MetricRule(drift=18.0, ceiling=1100.0),
            "reservoir_level": MetricRule(drift=0.5, ceiling=98.0),
            "outflow": MetricRule(drift=6.0, ceiling=700.0),
        },
        "water_treatment_plant": {
            "turbidity": MetricRule(drift=0.08, ceiling=6.0),
            "flow_rate": MetricRule(drift=4.0, ceiling=900.0),
        },
        "pump_station": {
            "flow_rate": MetricRule(drift=5.0, ceiling=1800.0),
        },
    },
    ScenarioKey.POWER_OUTAGE: {
        "pump_station": {
            "pump_speed": MetricRule(target=0.0, pull_rate=0.1),
            "pump_running": MetricRule(target=0.0, pull_rate=0.12),
            "flow_rate": MetricRule(drift=-15.0, floor=0.0),
            "pressure": MetricRule(drift=-0.1, floor=0.2),
        },
        "distribution_network": {
            "flow_rate": MetricRule(drift=-12.0, floor=5.0),
            "pressure": MetricRule(drift=-0.08, floor=0.3),
        },
        "water_treatment_plant": {
            "flow_rate": MetricRule(drift=-8.0, floor=50.0),
            "pressure": MetricRule(drift=-0.05, floor=0.5),
        },
    },
}


def apply_rule(value: float, rule: MetricRule, *, intensity: float) -> float:
    """Apply one gradual adjustment step at the given scenario intensity."""
    if intensity <= 0:
        return value

    adjusted = value
    if rule.drift:
        adjusted += rule.drift * intensity

    if rule.target is not None:
        adjusted += (rule.target - adjusted) * rule.pull_rate * intensity

    if rule.floor is not None:
        adjusted = max(rule.floor, adjusted)
    if rule.ceiling is not None:
        adjusted = min(rule.ceiling, adjusted)

    return adjusted


def rules_for(scenario_key: ScenarioKey, asset_type: str) -> dict[str, MetricRule]:
    scenario_rules = SCENARIO_RULES.get(scenario_key, {})
    return dict(scenario_rules.get(asset_type, {}))
