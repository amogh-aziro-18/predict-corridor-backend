"""Power seed catalog — POC substations, transformers, and feeders across Zones A–D."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.utilities.water.seed_catalog import CORRIDOR, ZONES

POWER_UTILITY = {
    "id": "util-power-01",
    "kind": "power",
    "name": "Power",
    "enabled": True,
}

# Stable Power asset IDs — must not collide with Water (WTP/PMP/TNK/…).
POWER_ASSET_IDS: tuple[str, ...] = (
    "SUB-A-01",
    "XFMR-A-01",
    "FDR-A-01",
    "SUB-B-01",
    "XFMR-B-01",
    "FDR-B-01",
    "SUB-C-01",
    "XFMR-C-01",
    "FDR-C-01",
    "SUB-D-01",
    "XFMR-D-01",
    "FDR-D-01",
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
        "utility_kind": "power",
        "type": asset_type,
        "latitude": round(lat0 + lat_offset, 6),
        "longitude": round(lon0 + lon_offset, 6),
        # Seed baseline only — not live health/scoring (Phase 14.3+).
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


POWER_ASSETS: list[dict[str, Any]] = [
    # Zone A
    _asset(
        asset_id="SUB-A-01",
        name="Zone A Primary Substation",
        zone="zone-a",
        asset_type="substation",
        lat_offset=0.004,
        lon_offset=0.003,
        criticality="very-high",
        risk="low",
        backup="redundant",
        recovery_priority=1,
        profile={"nominal_voltage_kv": 132.0, "capacity_mva": 80.0},
        last_maintenance=date(2026, 1, 12),
        next_inspection=date(2026, 7, 12),
    ),
    _asset(
        asset_id="XFMR-A-01",
        name="Zone A Power Transformer T1",
        zone="zone-a",
        asset_type="transformer",
        lat_offset=0.0045,
        lon_offset=0.0035,
        criticality="high",
        risk="low",
        backup="partial",
        recovery_priority=1,
        profile={"nominal_voltage_kv": 132.0, "rating_mva": 40.0},
        last_maintenance=date(2026, 2, 3),
        next_inspection=date(2026, 8, 3),
    ),
    _asset(
        asset_id="FDR-A-01",
        name="Zone A Distribution Feeder F1",
        zone="zone-a",
        asset_type="feeder",
        lat_offset=0.005,
        lon_offset=0.004,
        criticality="high",
        risk="low",
        backup="partial",
        recovery_priority=2,
        profile={"nominal_voltage_kv": 33.0, "rated_current_a": 600.0},
        last_maintenance=date(2026, 1, 20),
        next_inspection=date(2026, 7, 20),
    ),
    # Zone B
    _asset(
        asset_id="SUB-B-01",
        name="Zone B Primary Substation",
        zone="zone-b",
        asset_type="substation",
        lat_offset=0.004,
        lon_offset=-0.003,
        criticality="very-high",
        risk="low",
        backup="redundant",
        recovery_priority=1,
        profile={"nominal_voltage_kv": 132.0, "capacity_mva": 75.0},
        last_maintenance=date(2025, 12, 8),
        next_inspection=date(2026, 6, 8),
    ),
    _asset(
        asset_id="XFMR-B-01",
        name="Zone B Power Transformer T1",
        zone="zone-b",
        asset_type="transformer",
        lat_offset=0.0045,
        lon_offset=-0.0035,
        criticality="high",
        risk="low",
        backup="partial",
        recovery_priority=1,
        profile={"nominal_voltage_kv": 132.0, "rating_mva": 35.0},
        last_maintenance=date(2026, 1, 5),
        next_inspection=date(2026, 7, 5),
    ),
    _asset(
        asset_id="FDR-B-01",
        name="Zone B Distribution Feeder F1",
        zone="zone-b",
        asset_type="feeder",
        lat_offset=0.005,
        lon_offset=-0.004,
        criticality="high",
        risk="low",
        backup="partial",
        recovery_priority=2,
        profile={"nominal_voltage_kv": 33.0, "rated_current_a": 550.0},
        last_maintenance=date(2026, 2, 14),
        next_inspection=date(2026, 8, 14),
    ),
    # Zone C
    _asset(
        asset_id="SUB-C-01",
        name="Zone C Primary Substation",
        zone="zone-c",
        asset_type="substation",
        lat_offset=-0.003,
        lon_offset=0.004,
        criticality="very-high",
        risk="medium",
        backup="partial",
        recovery_priority=1,
        profile={"nominal_voltage_kv": 132.0, "capacity_mva": 70.0},
        last_maintenance=date(2025, 11, 18),
        next_inspection=date(2026, 5, 18),
    ),
    _asset(
        asset_id="XFMR-C-01",
        name="Zone C Power Transformer T1",
        zone="zone-c",
        asset_type="transformer",
        lat_offset=-0.0035,
        lon_offset=0.0045,
        criticality="high",
        risk="medium",
        backup="partial",
        recovery_priority=1,
        profile={"nominal_voltage_kv": 132.0, "rating_mva": 32.0},
        last_maintenance=date(2025, 12, 22),
        next_inspection=date(2026, 6, 22),
    ),
    _asset(
        asset_id="FDR-C-01",
        name="Zone C Distribution Feeder F1",
        zone="zone-c",
        asset_type="feeder",
        lat_offset=-0.004,
        lon_offset=0.005,
        criticality="high",
        risk="medium",
        backup="none",
        recovery_priority=2,
        profile={"nominal_voltage_kv": 33.0, "rated_current_a": 500.0},
        last_maintenance=date(2026, 1, 28),
        next_inspection=date(2026, 7, 28),
    ),
    # Zone D
    _asset(
        asset_id="SUB-D-01",
        name="Zone D Primary Substation",
        zone="zone-d",
        asset_type="substation",
        lat_offset=-0.004,
        lon_offset=-0.003,
        criticality="very-high",
        risk="medium",
        backup="partial",
        recovery_priority=1,
        profile={"nominal_voltage_kv": 132.0, "capacity_mva": 65.0},
        last_maintenance=date(2025, 10, 30),
        next_inspection=date(2026, 4, 30),
    ),
    _asset(
        asset_id="XFMR-D-01",
        name="Zone D Power Transformer T1",
        zone="zone-d",
        asset_type="transformer",
        lat_offset=-0.0045,
        lon_offset=-0.0035,
        criticality="high",
        risk="medium",
        backup="none",
        recovery_priority=1,
        profile={"nominal_voltage_kv": 132.0, "rating_mva": 30.0},
        last_maintenance=date(2025, 11, 9),
        next_inspection=date(2026, 5, 9),
    ),
    _asset(
        asset_id="FDR-D-01",
        name="Zone D Distribution Feeder F1",
        zone="zone-d",
        asset_type="feeder",
        lat_offset=-0.005,
        lon_offset=-0.004,
        criticality="high",
        risk="medium",
        backup="none",
        recovery_priority=2,
        profile={"nominal_voltage_kv": 33.0, "rated_current_a": 480.0},
        last_maintenance=date(2026, 2, 1),
        next_inspection=date(2026, 8, 1),
    ),
]

assert {a["id"] for a in POWER_ASSETS} == set(POWER_ASSET_IDS)
assert len(POWER_ASSETS) == 12
assert CORRIDOR["id"] == "corridor-01"
