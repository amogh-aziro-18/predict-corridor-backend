"""Communications seed catalog — POC edge PoPs and IoT gateways across Zones A–D.

Phase 17.2 — assets + profile nominals only. No telemetry. No health thresholds.
Numeric baselines are POC ASSUMPTION — NOT ENGINEERING STANDARD.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.utilities.water.seed_catalog import CORRIDOR, ZONES

COMMUNICATIONS_UTILITY = {
    "id": "util-communications-01",
    "kind": "communications",
    "name": "Communications",
    "enabled": True,
}

# Stable Communications asset IDs — must not collide with Water/Power/Gas prefixes.
COMMUNICATIONS_ASSET_IDS: tuple[str, ...] = (
    "POP-A-01",
    "GW-A-01",
    "POP-B-01",
    "GW-B-01",
    "POP-C-01",
    "GW-C-01",
    "POP-D-01",
    "GW-D-01",
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
        "utility_kind": "communications",
        "type": asset_type,
        "latitude": round(lat0 + lat_offset, 6),
        "longitude": round(lon0 + lon_offset, 6),
        # Seed baseline only — live health/condition updated in later Phase 17 slices.
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


def _edge_pop_profile(*, zone: str) -> dict[str, Any]:
    """
    POC ASSUMPTION — NOT ENGINEERING STANDARD.

    Nominals sized for a future Communications simulator (Phase 17.3), not SLA targets.
    """
    # Slightly warmer / higher draw in C/D for mixed-demo headroom later.
    temp = 28.0 if zone in {"zone-a", "zone-b"} else 32.0
    draw = 180.0 if zone in {"zone-a", "zone-b"} else 200.0
    return {
        "nominal_link_rtt_ms": 12.0,
        "nominal_packet_loss_pct": 0.2,
        "link_up": 1.0,
        "nominal_cabinet_temp_c": temp,
        "nominal_power_draw_w": draw,
    }


def _iot_gateway_profile(*, zone: str) -> dict[str, Any]:
    """
    POC ASSUMPTION — NOT ENGINEERING STANDARD.

    Gateways typically sit closer to field assets; slightly higher RTT baseline.
    """
    temp = 30.0 if zone in {"zone-a", "zone-b"} else 34.0
    draw = 45.0 if zone in {"zone-a", "zone-b"} else 55.0
    return {
        "nominal_link_rtt_ms": 18.0,
        "nominal_packet_loss_pct": 0.4,
        "link_up": 1.0,
        "nominal_cabinet_temp_c": temp,
        "nominal_power_draw_w": draw,
    }


COMMUNICATIONS_ASSETS: list[dict[str, Any]] = [
    # Zone A — offsets distinct from Gas/Power seed positions near the same origin.
    _asset(
        asset_id="POP-A-01",
        name="Zone A Edge PoP P1",
        zone="zone-a",
        asset_type="edge_pop",
        lat_offset=0.008,
        lon_offset=0.0045,
        criticality="very-high",
        risk="low",
        backup="partial",
        recovery_priority=1,
        profile=_edge_pop_profile(zone="zone-a"),
        last_maintenance=date(2026, 1, 18),
        next_inspection=date(2026, 7, 18),
    ),
    _asset(
        asset_id="GW-A-01",
        name="Zone A IoT Gateway G1",
        zone="zone-a",
        asset_type="iot_gateway",
        lat_offset=0.0085,
        lon_offset=0.005,
        criticality="high",
        risk="low",
        backup="none",
        recovery_priority=2,
        profile=_iot_gateway_profile(zone="zone-a"),
        last_maintenance=date(2026, 2, 4),
        next_inspection=date(2026, 8, 4),
    ),
    # Zone B
    _asset(
        asset_id="POP-B-01",
        name="Zone B Edge PoP P1",
        zone="zone-b",
        asset_type="edge_pop",
        lat_offset=0.008,
        lon_offset=-0.0045,
        criticality="very-high",
        risk="low",
        backup="partial",
        recovery_priority=1,
        profile=_edge_pop_profile(zone="zone-b"),
        last_maintenance=date(2025, 12, 14),
        next_inspection=date(2026, 6, 14),
    ),
    _asset(
        asset_id="GW-B-01",
        name="Zone B IoT Gateway G1",
        zone="zone-b",
        asset_type="iot_gateway",
        lat_offset=0.0085,
        lon_offset=-0.005,
        criticality="high",
        risk="low",
        backup="none",
        recovery_priority=2,
        profile=_iot_gateway_profile(zone="zone-b"),
        last_maintenance=date(2026, 1, 10),
        next_inspection=date(2026, 7, 10),
    ),
    # Zone C
    _asset(
        asset_id="POP-C-01",
        name="Zone C Edge PoP P1",
        zone="zone-c",
        asset_type="edge_pop",
        lat_offset=-0.007,
        lon_offset=0.0055,
        criticality="very-high",
        risk="medium",
        backup="partial",
        recovery_priority=1,
        profile=_edge_pop_profile(zone="zone-c"),
        last_maintenance=date(2025, 11, 22),
        next_inspection=date(2026, 5, 22),
    ),
    _asset(
        asset_id="GW-C-01",
        name="Zone C IoT Gateway G1",
        zone="zone-c",
        asset_type="iot_gateway",
        lat_offset=-0.0075,
        lon_offset=0.006,
        criticality="high",
        risk="medium",
        backup="none",
        recovery_priority=2,
        profile=_iot_gateway_profile(zone="zone-c"),
        last_maintenance=date(2025, 12, 20),
        next_inspection=date(2026, 6, 20),
    ),
    # Zone D
    _asset(
        asset_id="POP-D-01",
        name="Zone D Edge PoP P1",
        zone="zone-d",
        asset_type="edge_pop",
        lat_offset=-0.0075,
        lon_offset=-0.0045,
        criticality="very-high",
        risk="medium",
        backup="none",
        recovery_priority=1,
        profile=_edge_pop_profile(zone="zone-d"),
        last_maintenance=date(2025, 10, 30),
        next_inspection=date(2026, 4, 30),
    ),
    _asset(
        asset_id="GW-D-01",
        name="Zone D IoT Gateway G1",
        zone="zone-d",
        asset_type="iot_gateway",
        lat_offset=-0.008,
        lon_offset=-0.005,
        criticality="high",
        risk="medium",
        backup="none",
        recovery_priority=2,
        profile=_iot_gateway_profile(zone="zone-d"),
        last_maintenance=date(2025, 11, 15),
        next_inspection=date(2026, 5, 15),
    ),
]

assert {a["id"] for a in COMMUNICATIONS_ASSETS} == set(COMMUNICATIONS_ASSET_IDS)
assert len(COMMUNICATIONS_ASSETS) == 8
assert CORRIDOR["id"] == "corridor-01"
