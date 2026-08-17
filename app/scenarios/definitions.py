"""Operational scenario catalog for the water utility simulator."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ScenarioKey(StrEnum):
    NORMAL = "normal_operations"
    PEAK_DEMAND = "peak_demand"
    PUMP_FAILURE = "pump_failure"
    PIPELINE_LEAK = "pipeline_leak"
    RESERVOIR_LOW = "reservoir_low"
    CONTAMINATION = "water_contamination"
    HEAVY_RAIN = "heavy_rain_flood"
    POWER_OUTAGE = "power_outage"


@dataclass(frozen=True)
class ScenarioDefinition:
    key: ScenarioKey
    name: str
    description: str
    category: str
    default_severity: str


SCENARIO_DEFINITIONS: dict[ScenarioKey, ScenarioDefinition] = {
    ScenarioKey.NORMAL: ScenarioDefinition(
        key=ScenarioKey.NORMAL,
        name="Normal Operations",
        description="Baseline corridor operations with nominal demand and stable assets.",
        category="operations",
        default_severity="low",
    ),
    ScenarioKey.PEAK_DEMAND: ScenarioDefinition(
        key=ScenarioKey.PEAK_DEMAND,
        name="Peak Demand",
        description="Elevated consumption during industrial peak hours.",
        category="demand",
        default_severity="medium",
    ),
    ScenarioKey.PUMP_FAILURE: ScenarioDefinition(
        key=ScenarioKey.PUMP_FAILURE,
        name="Pump Failure",
        description="Primary pump degradation leading to pressure and flow loss.",
        category="equipment",
        default_severity="high",
    ),
    ScenarioKey.PIPELINE_LEAK: ScenarioDefinition(
        key=ScenarioKey.PIPELINE_LEAK,
        name="Pipeline Leak",
        description="Distribution leak increasing outflow and reducing downstream pressure.",
        category="infrastructure",
        default_severity="high",
    ),
    ScenarioKey.RESERVOIR_LOW: ScenarioDefinition(
        key=ScenarioKey.RESERVOIR_LOW,
        name="Reservoir Low",
        description="Storage drawdown from sustained demand exceeding inflow.",
        category="storage",
        default_severity="high",
    ),
    ScenarioKey.CONTAMINATION: ScenarioDefinition(
        key=ScenarioKey.CONTAMINATION,
        name="Water Contamination",
        description="Elevated turbidity and depressed chlorine residual in treatment path.",
        category="quality",
        default_severity="critical",
    ),
    ScenarioKey.HEAVY_RAIN: ScenarioDefinition(
        key=ScenarioKey.HEAVY_RAIN,
        name="Heavy Rain / Flood",
        description="Storm inflow surge stressing reservoirs and treatment capacity.",
        category="weather",
        default_severity="high",
    ),
    ScenarioKey.POWER_OUTAGE: ScenarioDefinition(
        key=ScenarioKey.POWER_OUTAGE,
        name="Power Outage",
        description="Grid loss stopping pumps and collapsing distribution pressure.",
        category="resilience",
        default_severity="critical",
    ),
}


def all_scenarios() -> list[ScenarioDefinition]:
    return list(SCENARIO_DEFINITIONS.values())


def get_scenario(key: str) -> ScenarioDefinition:
    try:
        parsed = ScenarioKey(key)
    except ValueError as exc:
        raise KeyError(key) from exc
    return SCENARIO_DEFINITIONS[parsed]
