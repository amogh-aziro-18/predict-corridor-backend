"""Database seed entrypoint — corridor, zones, water/power/gas/communications utilities, assets, sensors, technicians, work orders."""

from __future__ import annotations

from sqlalchemy import delete

from app.core.logging import configure_logging, get_logger
from app.db.seed.dependencies_catalog import DEPENDENCY_EDGES
from app.db.seed.technicians_catalog import TECHNICIANS
from app.db.seed.work_orders_catalog import (
    SEED_CREATED_BY,
    SEED_WORK_ORDER_SOURCE,
    SEED_WORK_ORDER_STATUS,
    WORK_ORDERS,
)
from app.db.session import SessionLocal
from app.domain.models import (
    Asset,
    AssetDependency,
    Corridor,
    Sensor,
    Technician,
    Utility,
    WorkOrder,
    Zone,
)
from app.utilities.communications import metrics as communications_metrics
from app.utilities.communications.seed_catalog import (
    COMMUNICATIONS_ASSETS,
    COMMUNICATIONS_UTILITY,
)
from app.utilities.gas import metrics as gas_metrics
from app.utilities.gas.seed_catalog import GAS_ASSETS, GAS_UTILITY
from app.utilities.power import metrics as power_metrics
from app.utilities.power.seed_catalog import POWER_ASSETS, POWER_UTILITY
from app.utilities.registry import bootstrap_plugins
from app.utilities.water import metrics as water_metrics
from app.utilities.water.seed_catalog import CORRIDOR, WATER_ASSETS, WATER_UTILITY, ZONES

logger = get_logger(__name__)


def _zone_utility_kinds(base: list[str]) -> list[str]:
    """Preserve catalog kinds and ensure live utility kinds are associated."""
    kinds: list[str] = []
    for kind in [*base, "water", "power", "gas", "communications"]:
        if kind not in kinds:
            kinds.append(kind)
    return kinds


def _merge_asset_and_sensors(
    db,
    *,
    item: dict,
    metrics_for_type,
    counts: dict[str, int],
) -> None:
    asset = Asset(
        id=item["id"],
        name=item["name"],
        corridor_id=CORRIDOR["id"],
        zone_id=item["zone_id"],
        utility_kind=item["utility_kind"],
        type=item["type"],
        latitude=item["latitude"],
        longitude=item["longitude"],
        status=item["status"],
        health=item["health"],
        risk=item["risk"],
        weather_exposure=item["weather_exposure"],
        flood_risk=item["flood_risk"],
        criticality=item["criticality"],
        backup=item["backup"],
        recovery_priority=item["recovery_priority"],
        last_maintenance=item["last_maintenance"],
        next_inspection=item["next_inspection"],
        predicted_failure_days=item["predicted_failure_days"],
        confidence=item["confidence"],
        profile=dict(item["profile"]),
    )
    db.merge(asset)
    counts["assets"] += 1

    for metric in metrics_for_type(item["type"]):
        sensor_id = f"SNS-{item['id']}-{metric['key']}"
        db.merge(
            Sensor(
                id=sensor_id,
                asset_id=item["id"],
                name=f"{item['name']} · {metric['label']}",
                metric_key=metric["key"],
                unit=metric["unit"],
                enabled=True,
            )
        )
        counts["sensors"] += 1


def seed_all(*, reset_water_assets: bool = False) -> dict[str, int]:
    """
    Idempotent seed of corridor reference data
    (Water + Power + Gas + Communications foundation).

    Uses merge/upsert semantics so re-running is safe.
    Work-order seed rows are insert-if-missing only (lifecycle preserved).
    """
    bootstrap_plugins(["water", "power", "gas", "communications"])
    db = SessionLocal()
    counts = {
        "corridors": 0,
        "zones": 0,
        "utilities": 0,
        "assets": 0,
        "sensors": 0,
        "technicians": 0,
        "work_orders": 0,
        "dependencies": 0,
    }
    try:
        corridor = Corridor(
            id=CORRIDOR["id"],
            name=CORRIDOR["name"],
            authority=CORRIDOR["authority"],
            length_km=CORRIDOR["length_km"],
        )
        db.merge(corridor)
        counts["corridors"] = 1

        for z in ZONES:
            db.merge(
                Zone(
                    id=z["id"],
                    corridor_id=CORRIDOR["id"],
                    name=z["name"],
                    code=z["code"],
                    area_sq_km=z["area_sq_km"],
                    health=z["health"],
                    population=z["population"],
                    critical_assets=z["critical_assets"],
                    utility_kinds=_zone_utility_kinds(list(z["utility_kinds"])),
                )
            )
        counts["zones"] = len(ZONES)

        db.merge(
            Utility(
                id=WATER_UTILITY["id"],
                corridor_id=CORRIDOR["id"],
                kind=WATER_UTILITY["kind"],
                name=WATER_UTILITY["name"],
                enabled=WATER_UTILITY["enabled"],
            )
        )
        db.merge(
            Utility(
                id=POWER_UTILITY["id"],
                corridor_id=CORRIDOR["id"],
                kind=POWER_UTILITY["kind"],
                name=POWER_UTILITY["name"],
                enabled=POWER_UTILITY["enabled"],
            )
        )
        db.merge(
            Utility(
                id=GAS_UTILITY["id"],
                corridor_id=CORRIDOR["id"],
                kind=GAS_UTILITY["kind"],
                name=GAS_UTILITY["name"],
                enabled=GAS_UTILITY["enabled"],
            )
        )
        db.merge(
            Utility(
                id=COMMUNICATIONS_UTILITY["id"],
                corridor_id=CORRIDOR["id"],
                kind=COMMUNICATIONS_UTILITY["kind"],
                name=COMMUNICATIONS_UTILITY["name"],
                enabled=COMMUNICATIONS_UTILITY["enabled"],
            )
        )
        counts["utilities"] = 4

        if reset_water_assets:
            db.execute(delete(Asset).where(Asset.utility_kind == "water"))
            db.flush()

        for item in WATER_ASSETS:
            _merge_asset_and_sensors(
                db,
                item=item,
                metrics_for_type=water_metrics.metrics_for_asset_type,
                counts=counts,
            )

        for item in POWER_ASSETS:
            _merge_asset_and_sensors(
                db,
                item=item,
                metrics_for_type=power_metrics.metrics_for_asset_type,
                counts=counts,
            )

        for item in GAS_ASSETS:
            _merge_asset_and_sensors(
                db,
                item=item,
                metrics_for_type=gas_metrics.metrics_for_asset_type,
                counts=counts,
            )

        for item in COMMUNICATIONS_ASSETS:
            _merge_asset_and_sensors(
                db,
                item=item,
                metrics_for_type=communications_metrics.metrics_for_asset_type,
                counts=counts,
            )

        for item in TECHNICIANS:
            kinds = list(item.get("utility_kinds") or ["water"])
            existing = db.get(Technician, item["id"])
            if existing is not None:
                # Preserve live availability; refresh identity + domain association.
                existing.name = item["name"]
                existing.role = item["role"]
                existing.primary_zone_id = item["primary_zone_id"]
                existing.skills = list(item["skills"])
                existing.utility_kinds = kinds
                existing.emergency_certified = bool(item["emergency_certified"])
            else:
                db.add(
                    Technician(
                        id=item["id"],
                        name=item["name"],
                        role=item["role"],
                        primary_zone_id=item["primary_zone_id"],
                        skills=list(item["skills"]),
                        utility_kinds=kinds,
                        availability=item["availability"],
                        emergency_certified=bool(item["emergency_certified"]),
                    )
                )
        counts["technicians"] = len(TECHNICIANS)

        db.flush()

        for item in WORK_ORDERS:
            asset = db.get(Asset, item["asset_id"])
            if asset is None:
                raise ValueError(
                    f"Work order seed references missing asset: {item['asset_id']}"
                )
            existing = db.get(WorkOrder, item["id"])
            if existing is None:
                db.add(
                    WorkOrder(
                        id=item["id"],
                        asset_id=asset.id,
                        utility=asset.utility_kind,
                        zone_id=asset.zone_id,
                        title=item["title"],
                        description=item.get("description"),
                        priority=item.get("priority", "medium"),
                        status=SEED_WORK_ORDER_STATUS,
                        assigned_technician_id=None,
                        source=SEED_WORK_ORDER_SOURCE,
                        created_by=SEED_CREATED_BY,
                        recommendation_id=None,
                        recommendation_snapshot=None,
                    )
                )
            # If the seed WO already exists, leave lifecycle / assignment untouched.
            counts["work_orders"] += 1

        # Dependencies require Water/Power/Gas assets already merged above.
        for item in DEPENDENCY_EDGES:
            source = db.get(Asset, item["source_asset_id"])
            target = db.get(Asset, item["target_asset_id"])
            if source is None:
                raise ValueError(
                    f"Dependency seed references missing source asset: {item['source_asset_id']}"
                )
            if target is None:
                raise ValueError(
                    f"Dependency seed references missing target asset: {item['target_asset_id']}"
                )
            if source.id == target.id:
                raise ValueError(
                    f"Dependency seed self-reference not allowed: {item['id']}"
                )
            db.merge(
                AssetDependency(
                    id=item["id"],
                    source_asset_id=source.id,
                    target_asset_id=target.id,
                    dependency_type=item["dependency_type"],
                    enabled=bool(item.get("enabled", True)),
                )
            )
            counts["dependencies"] += 1

        db.commit()
        logger.info("Seed complete: %s", counts)
        return counts
    except Exception:
        db.rollback()
        logger.exception("Seed failed")
        raise
    finally:
        db.close()


def main() -> None:
    configure_logging("INFO")
    counts = seed_all()
    print(f"Seeded: {counts}")


if __name__ == "__main__":
    main()
