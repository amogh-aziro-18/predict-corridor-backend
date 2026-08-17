"""Water metric catalog and sensor mapping helpers."""

from __future__ import annotations

from typing import Any

# Canonical metric keys for the water utility (used by sensors, MQTT, simulator).
WATER_METRICS: dict[str, dict[str, str]] = {
    "flow_rate": {"unit": "L/s", "label": "Flow rate"},
    "pressure": {"unit": "bar", "label": "Pressure"},
    "tank_level": {"unit": "%", "label": "Tank level"},
    "reservoir_level": {"unit": "%", "label": "Reservoir level"},
    "inflow": {"unit": "L/s", "label": "Inflow"},
    "outflow": {"unit": "L/s", "label": "Outflow"},
    "pump_speed": {"unit": "rpm", "label": "Pump speed"},
    "pump_running": {"unit": "bool", "label": "Pump running"},
    "chlorine_level": {"unit": "mg/L", "label": "Chlorine residual"},
    "turbidity": {"unit": "NTU", "label": "Turbidity"},
    "valve_position": {"unit": "%", "label": "Valve position"},
}

# Which metrics apply to each water asset type.
ASSET_TYPE_METRICS: dict[str, list[str]] = {
    "water_treatment_plant": ["flow_rate", "pressure", "chlorine_level", "turbidity"],
    "pump_station": ["flow_rate", "pressure", "pump_speed", "pump_running"],
    "storage_tank": ["tank_level", "pressure"],
    "reservoir": ["reservoir_level", "inflow", "outflow"],
    "valve_network": ["valve_position", "pressure"],
    "distribution_network": ["flow_rate", "pressure"],
    "pressure_monitoring_station": ["pressure"],
    "flow_meter": ["flow_rate"],
}

# Metric key → Asset.profile field for live state mirroring.
PROFILE_FIELD_BY_METRIC: dict[str, str] = {
    "flow_rate": "flow_rate",
    "pressure": "pressure",
    "tank_level": "current_level",
    "reservoir_level": "current_level",
    "inflow": "inflow",
    "outflow": "outflow",
    "pump_speed": "pump_speed",
    "pump_running": "pump_running",
    "chlorine_level": "chlorine_level",
    "turbidity": "turbidity",
    "valve_position": "valve_position",
}


def metrics_for_asset_type(asset_type: str) -> list[dict[str, Any]]:
    keys = ASSET_TYPE_METRICS.get(asset_type, ["flow_rate", "pressure"])
    return [{"key": k, **WATER_METRICS[k]} for k in keys if k in WATER_METRICS]


def catalog() -> list[dict[str, Any]]:
    return [{"key": k, **v} for k, v in WATER_METRICS.items()]
