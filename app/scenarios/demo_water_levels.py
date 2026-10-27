"""Demo helpers: seed starting water levels + healthy pressure for asymmetric demo.

Does not invent scenario keys — only sets storage/pressure profile fields so the
simulator has a repeatable starting point. Ongoing drift still comes from
ScenarioEngine + WaterTelemetryGenerator → DB → /dashboard/water.
"""

from __future__ import annotations

from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm import Session

from app.domain.repositories import AssetRepository

# Starting mean levels for storage/reservoir assets when asymmetric demo starts.
DEMO_ZONE_LEVEL_SEEDS: dict[str, float] = {
    "zone-a": 84.0,  # healthy band ~75–90
    "zone-b": 81.0,  # healthy band ~72–88
    "zone-c": 66.0,  # degrading trajectory
    "zone-d": 36.0,  # critical trajectory (below 40)
}

# Healthy pressure for normal zones; stressed zones keep scenario-driven pressure.
DEMO_ZONE_PRESSURE_SEEDS: dict[str, float] = {
    "zone-a": 3.15,
    "zone-b": 3.05,
    "zone-c": 2.4,
    "zone-d": 1.9,
}

_LEVEL_ASSET_TYPES = frozenset({"storage_tank", "reservoir"})
_PRESSURE_ASSET_TYPES = frozenset(
    {
        "storage_tank",
        "reservoir",
        "pump_station",
        "distribution_network",
        "pressure_monitoring_station",
        "water_treatment_plant",
        "valve_network",
    }
)


def seed_asymmetric_demo_levels(db: Session) -> int:
    """
    Reset water storage levels and normalize A/B pressure for a clean demo start.

    Returns the number of assets updated.
    """
    assets = AssetRepository(db).list(utility_kind="water")
    updated = 0
    for asset in assets:
        profile = dict(asset.profile or {})
        changed = False

        if asset.type in _LEVEL_ASSET_TYPES:
            target = DEMO_ZONE_LEVEL_SEEDS.get(asset.zone_id)
            if target is not None:
                jitter = ((sum(ord(c) for c in asset.id) % 7) - 3) * 0.5
                level = max(5.0, min(100.0, round(target + jitter, 1)))
                profile["current_level"] = level
                changed = True

        pressure_target = DEMO_ZONE_PRESSURE_SEEDS.get(asset.zone_id)
        if pressure_target is not None and asset.type in _PRESSURE_ASSET_TYPES:
            pj = ((sum(ord(c) for c in asset.id) % 5) - 2) * 0.04
            profile["pressure"] = round(max(0.5, pressure_target + pj), 2)
            if asset.zone_id in {"zone-a", "zone-b"}:
                # Restore healthy pump state for normal zones.
                if asset.type == "pump_station":
                    profile["pump_speed"] = 1450.0
                    profile["pump_running"] = 1.0
                    profile["flow_rate"] = float(profile.get("flow_rate") or 500.0)
                    if float(profile["flow_rate"]) < 120:
                        profile["flow_rate"] = 480.0
            changed = True

        if changed:
            asset.profile = profile
            flag_modified(asset, "profile")
            updated += 1

    if updated:
        db.commit()
    return updated
