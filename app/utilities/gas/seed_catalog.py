"""Gas seed catalog — POC compressors, regulators, and feeders across Zones A–D."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.utilities.water.seed_catalog import CORRIDOR, ZONES

GAS_UTILITY = {
    "id": "util-gas-01",
    "kind": "gas",
    "name": "Gas",
    "enabled": True,
}

# Stable Gas asset IDs — must not collide with Water or Power prefixes.
GAS_ASSET_IDS: tuple[str, ...] = (
    "CMP-A-01",
    "REG-A-01",
    "GFDR-A-01",
    "CMP-B-01",
    "REG-B-01",
    "GFDR-B-01",
    "CMP-C-01",
    "REG-C-01",
    "GFDR-C-01",
    "CMP-D-01",
    "REG-D-01",
    "GFDR-D-01",
)


def _zone_origin(zone_id: str) -> tuple[float, float]:
    for z in ZONES:
        if z["id"] == zone_id:
            origin = z.get("origin")
            if isinstance(origin, tuple) and len(origin) == 2:
                return float(origin[0]), float(origin[1])
    return 28.6120, 77.2090


def _asset(
    *,
    asset_id: str,
    name: str,
    zone: str,
    asset_type: str,
    lat_offset: float,
    lon_offset: float,
    criticality: str,
    risk: str,
    backup: str,
    recovery_priority: int,
    profile: dict[str, Any],
    last_maintenance: date,
    next_inspection: date,
) -> dict[str, Any]:
    lat0, lon0 = _zone_origin(zone)
    return {
        "id": asset_id,
        "name": name,
        "zone_id": zone,
        "utility_kind": "gas",
        "type": asset_type,
        "latitude": round(lat0 + lat_offset, 6),
        "longitude": round(lon0 + lon_offset, 6),
        # Seed baseline only — live health/condition updated by Phase 15.1 assessment.
        "status": "operational",
        "health": 100.0,
        "risk": risk,
        "weather_exposure": "medium" if zone == "zone-c" else "low",
        "flood_risk": "medium" if zone in {"zone-b", "zone-c"} else "low",
        "criticality": criticality,
        "backup": backup,
        "recovery_priority": recovery_priority,
        "last_maintenance": last_maintenance,
        "next_inspection": next_inspection,
        "predicted_failure_days": None,
        "confidence": 0.0,
        "profile": profile,
    }


def _compressor_profile(*, zone: str) -> dict[str, Any]:
    # Milder nominals in C/D leave headroom for degrading drift demos.
    load = 55.0 if zone in {"zone-a", "zone-b"} else 62.0
    return {
        "nominal_pressure_bar": 28.0,
        "nominal_flow_nm3_h": 4200.0,
        "nominal_temperature_c": 45.0,
        "nominal_compressor_load_pct": load,
        "nominal_vibration_mm_s": 2.0,
        "nominal_discharge_pressure_bar": 32.0,
        "nominal_power_kw": 380.0,
        "nominal_gas_quality_pct": 96.0,
    }


def _regulator_profile() -> dict[str, Any]:
    return {
        "nominal_pressure_bar": 8.5,
        "nominal_flow_nm3_h": 1800.0,
        "nominal_temperature_c": 28.0,
        "nominal_valve_position_pct": 55.0,
        "nominal_gas_quality_pct": 96.0,
    }


def _feeder_profile() -> dict[str, Any]:
    return {
        "nominal_pressure_bar": 6.0,
        "nominal_flow_nm3_h": 950.0,
        "nominal_temperature_c": 26.0,
        "nominal_gas_quality_pct": 95.5,
    }


GAS_ASSETS: list[dict[str, Any]] = [
    # Zone A
    _asset(
        asset_id="CMP-A-01",
        name="Zone A Gas Compressor C1",
        zone="zone-a",
        asset_type="gas_compressor",
        lat_offset=0.006,
        lon_offset=0.0025,
        criticality="very-high",
        risk="low",
        backup="partial",
        recovery_priority=1,
        profile=_compressor_profile(zone="zone-a"),
        last_maintenance=date(2026, 1, 15),
        next_inspection=date(2026, 7, 15),
    ),
    _asset(
        asset_id="REG-A-01",
        name="Zone A Gas Regulator Station R1",
        zone="zone-a",
        asset_type="gas_regulator_station",
        lat_offset=0.0065,
        lon_offset=0.003,
        criticality="high",
        risk="low",
        backup="partial",
        recovery_priority=1,
        profile=_regulator_profile(),
        last_maintenance=date(2026, 2, 1),
        next_inspection=date(2026, 8, 1),
    ),
    _asset(
        asset_id="GFDR-A-01",
        name="Zone A Gas Feeder F1",
        zone="zone-a",
        asset_type="gas_feeder",
        lat_offset=0.007,
        lon_offset=0.0035,
        criticality="high",
        risk="low",
        backup="none",
        recovery_priority=2,
        profile=_feeder_profile(),
        last_maintenance=date(2026, 1, 22),
        next_inspection=date(2026, 7, 22),
    ),
    # Zone B
    _asset(
        asset_id="CMP-B-01",
        name="Zone B Gas Compressor C1",
        zone="zone-b",
        asset_type="gas_compressor",
        lat_offset=0.006,
        lon_offset=-0.0025,
        criticality="very-high",
        risk="low",
        backup="partial",
        recovery_priority=1,
        profile=_compressor_profile(zone="zone-b"),
        last_maintenance=date(2025, 12, 10),
        next_inspection=date(2026, 6, 10),
    ),
    _asset(
        asset_id="REG-B-01",
        name="Zone B Gas Regulator Station R1",
        zone="zone-b",
        asset_type="gas_regulator_station",
        lat_offset=0.0065,
        lon_offset=-0.003,
        criticality="high",
        risk="low",
        backup="partial",
        recovery_priority=1,
        profile=_regulator_profile(),
        last_maintenance=date(2026, 1, 8),
        next_inspection=date(2026, 7, 8),
    ),
    _asset(
        asset_id="GFDR-B-01",
        name="Zone B Gas Feeder F1",
        zone="zone-b",
        asset_type="gas_feeder",
        lat_offset=0.007,
        lon_offset=-0.0035,
        criticality="high",
        risk="low",
        backup="none",
        recovery_priority=2,
        profile=_feeder_profile(),
        last_maintenance=date(2026, 2, 12),
        next_inspection=date(2026, 8, 12),
    ),
    # Zone C
    _asset(
        asset_id="CMP-C-01",
        name="Zone C Gas Compressor C1",
        zone="zone-c",
        asset_type="gas_compressor",
        lat_offset=-0.005,
        lon_offset=0.0035,
        criticality="very-high",
        risk="medium",
        backup="partial",
        recovery_priority=1,
        profile=_compressor_profile(zone="zone-c"),
        last_maintenance=date(2025, 11, 20),
        next_inspection=date(2026, 5, 20),
    ),
    _asset(
        asset_id="REG-C-01",
        name="Zone C Gas Regulator Station R1",
        zone="zone-c",
        asset_type="gas_regulator_station",
        lat_offset=-0.0055,
        lon_offset=0.004,
        criticality="high",
        risk="medium",
        backup="none",
        recovery_priority=1,
        profile=_regulator_profile(),
        last_maintenance=date(2025, 12, 18),
        next_inspection=date(2026, 6, 18),
    ),
    _asset(
        asset_id="GFDR-C-01",
        name="Zone C Gas Feeder F1",
        zone="zone-c",
        asset_type="gas_feeder",
        lat_offset=-0.006,
        lon_offset=0.0045,
        criticality="high",
        risk="medium",
        backup="none",
        recovery_priority=2,
        profile=_feeder_profile(),
        last_maintenance=date(2026, 1, 30),
        next_inspection=date(2026, 7, 30),
    ),
    # Zone D
    _asset(
        asset_id="CMP-D-01",
        name="Zone D Gas Compressor C1",
        zone="zone-d",
        asset_type="gas_compressor",
        lat_offset=-0.0055,
        lon_offset=-0.0025,
        criticality="very-high",
        risk="medium",
        backup="none",
        recovery_priority=1,
        profile=_compressor_profile(zone="zone-d"),
        last_maintenance=date(2025, 10, 28),
        next_inspection=date(2026, 4, 28),
    ),
    _asset(
        asset_id="REG-D-01",
        name="Zone D Gas Regulator Station R1",
        zone="zone-d",
        asset_type="gas_regulator_station",
        lat_offset=-0.006,
        lon_offset=-0.003,
        criticality="high",
        risk="medium",
        backup="none",
        recovery_priority=1,
        profile=_regulator_profile(),
        last_maintenance=date(2025, 11, 12),
        next_inspection=date(2026, 5, 12),
    ),
    _asset(
        asset_id="GFDR-D-01",
        name="Zone D Gas Feeder F1",
        zone="zone-d",
        asset_type="gas_feeder",
        lat_offset=-0.0065,
        lon_offset=-0.0035,
        criticality="high",
        risk="medium",
        backup="none",
        recovery_priority=2,
        profile=_feeder_profile(),
        last_maintenance=date(2026, 2, 5),
        next_inspection=date(2026, 8, 5),
    ),
]

assert {a["id"] for a in GAS_ASSETS} == set(GAS_ASSET_IDS)
assert len(GAS_ASSETS) == 12
assert CORRIDOR["id"] == "corridor-01"
