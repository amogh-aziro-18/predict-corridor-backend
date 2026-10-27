"""Gas metric catalog and sensor mapping helpers.

Threshold / condition bands live in ``app.utilities.gas.health`` (Phase 15.1).
"""

from __future__ import annotations

from typing import Any

# Canonical Gas metric keys (sensors, MQTT, simulator). Distinct from Water/Power keys.
GAS_METRICS: dict[str, dict[str, str]] = {
    "pressure_bar": {"unit": "bar", "label": "Pressure"},
    "flow_rate_nm3_h": {"unit": "Nm³/h", "label": "Flow rate"},
    "temperature_c": {"unit": "°C", "label": "Temperature"},
    "compressor_load_pct": {"unit": "%", "label": "Compressor load"},
    "vibration_mm_s": {"unit": "mm/s", "label": "Vibration"},
    # Synthetic 0–1 POC signal — NOT certified methane concentration.
    "leak_indicator": {"unit": "0-1", "label": "Leak indicator (POC)"},
    "gas_quality_pct": {"unit": "%", "label": "Gas quality"},
    "valve_position_pct": {"unit": "%", "label": "Valve position"},
    "compressor_discharge_pressure_bar": {
        "unit": "bar",
        "label": "Compressor discharge pressure",
    },
    "power_consumption_kw": {"unit": "kW", "label": "Power consumption"},
}

# Which metrics apply to each Gas asset type.
ASSET_TYPE_METRICS: dict[str, list[str]] = {
    "gas_compressor": [
        "pressure_bar",
        "flow_rate_nm3_h",
        "temperature_c",
        "compressor_load_pct",
        "vibration_mm_s",
        "leak_indicator",
        "compressor_discharge_pressure_bar",
        "power_consumption_kw",
        "gas_quality_pct",
    ],
    "gas_regulator_station": [
        "pressure_bar",
        "flow_rate_nm3_h",
        "temperature_c",
        "valve_position_pct",
        "leak_indicator",
        "gas_quality_pct",
    ],
    "gas_feeder": [
        "pressure_bar",
        "flow_rate_nm3_h",
        "temperature_c",
        "leak_indicator",
        "gas_quality_pct",
    ],
}

# Metric key → Asset.profile field for live state mirroring.
PROFILE_FIELD_BY_METRIC: dict[str, str] = {
    "pressure_bar": "pressure_bar",
    "flow_rate_nm3_h": "flow_rate_nm3_h",
    "temperature_c": "temperature_c",
    "compressor_load_pct": "compressor_load_pct",
    "vibration_mm_s": "vibration_mm_s",
    "leak_indicator": "leak_indicator",
    "gas_quality_pct": "gas_quality_pct",
    "valve_position_pct": "valve_position_pct",
    "compressor_discharge_pressure_bar": "compressor_discharge_pressure_bar",
    "power_consumption_kw": "power_consumption_kw",
}


def metrics_for_asset_type(asset_type: str) -> list[dict[str, Any]]:
    keys = ASSET_TYPE_METRICS.get(asset_type, ["pressure_bar", "flow_rate_nm3_h"])
    return [{"key": k, **GAS_METRICS[k]} for k in keys if k in GAS_METRICS]


def catalog() -> list[dict[str, Any]]:
    return [{"key": k, **v} for k, v in GAS_METRICS.items()]
