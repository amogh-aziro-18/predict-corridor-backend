"""Power metric catalog and sensor mapping helpers.

Threshold / normal / degrading ranges are intentionally NOT defined here.
They will be established during Phase 14.3 simulator baselining.
"""

from __future__ import annotations

from typing import Any

# Canonical metric keys for the power utility (sensors, MQTT, future simulator).
POWER_METRICS: dict[str, dict[str, str]] = {
    "voltage_kv": {"unit": "kV", "label": "Voltage"},
    "current_a": {"unit": "A", "label": "Current"},
    "active_power_mw": {"unit": "MW", "label": "Active power"},
    "load_pct": {"unit": "%", "label": "Load"},
    "frequency_hz": {"unit": "Hz", "label": "Frequency"},
    "oil_temp_c": {"unit": "°C", "label": "Oil temperature"},
    "power_factor": {"unit": "pf", "label": "Power factor"},
}

# Which metrics apply to each power asset type.
ASSET_TYPE_METRICS: dict[str, list[str]] = {
    "substation": [
        "voltage_kv",
        "frequency_hz",
        "active_power_mw",
        "load_pct",
        "power_factor",
    ],
    "transformer": [
        "voltage_kv",
        "load_pct",
        "oil_temp_c",
        "active_power_mw",
        "power_factor",
    ],
    "feeder": [
        "voltage_kv",
        "current_a",
        "active_power_mw",
        "load_pct",
        "power_factor",
    ],
}

# Metric key → Asset.profile field for future live state mirroring (Phase 14.3+).
PROFILE_FIELD_BY_METRIC: dict[str, str] = {
    "voltage_kv": "voltage_kv",
    "current_a": "current_a",
    "active_power_mw": "active_power_mw",
    "load_pct": "load_pct",
    "frequency_hz": "frequency_hz",
    "oil_temp_c": "oil_temp_c",
    "power_factor": "power_factor",
}


def metrics_for_asset_type(asset_type: str) -> list[dict[str, Any]]:
    keys = ASSET_TYPE_METRICS.get(asset_type, ["voltage_kv", "load_pct"])
    return [{"key": k, **POWER_METRICS[k]} for k in keys if k in POWER_METRICS]


def catalog() -> list[dict[str, Any]]:
    return [{"key": k, **v} for k, v in POWER_METRICS.items()]
