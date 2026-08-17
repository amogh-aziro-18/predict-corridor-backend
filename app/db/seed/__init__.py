"""Database seed entrypoint — corridor, zones, water utility, assets, sensors."""

from __future__ import annotations

from sqlalchemy import delete

from app.core.logging import configure_logging, get_logger
from app.db.session import SessionLocal
from app.domain.models import Asset, Corridor, Sensor, Utility, Zone
from app.utilities.registry import bootstrap_plugins
from app.utilities.water import metrics as water_metrics
from app.utilities.water.seed_catalog import CORRIDOR, WATER_ASSETS, WATER_UTILITY, ZONES

logger = get_logger(__name__)


def seed_all(*, reset_water_assets: bool = False) -> dict[str, int]:
    """
    Idempotent seed of Sprint 2 reference data.

    Uses merge/upsert semantics so re-running is safe.
    """
    bootstrap_plugins(["water"])
    db = SessionLocal()
    counts = {"corridors": 0, "zones": 0, "utilities": 0, "assets": 0, "sensors": 0}
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
                    utility_kinds=list(z["utility_kinds"]),
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
        counts["utilities"] = 1

        if reset_water_assets:
            db.execute(delete(Asset).where(Asset.utility_kind == "water"))
            db.flush()

        for item in WATER_ASSETS:
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

            for metric in water_metrics.metrics_for_asset_type(item["type"]):
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
