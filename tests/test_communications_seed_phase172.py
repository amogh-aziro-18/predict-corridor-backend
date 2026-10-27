"""Phase 17.2 — Communications asset/sensor seed foundation."""

from __future__ import annotations

from app.db.seed import seed_all
from app.db.session import SessionLocal
from app.domain.models import Asset, Sensor, Utility, Zone
from app.utilities.communications.metrics import ASSET_TYPE_METRICS, COMMUNICATIONS_METRICS
from app.utilities.communications.seed_catalog import (
    COMMUNICATIONS_ASSET_IDS,
    COMMUNICATIONS_ASSETS,
    COMMUNICATIONS_UTILITY,
)
from app.utilities.gas.seed_catalog import GAS_ASSET_IDS, GAS_ASSETS
from app.utilities.power.seed_catalog import POWER_ASSET_IDS, POWER_ASSETS
from app.utilities.water.seed_catalog import WATER_ASSETS

EXPECTED_COMM_TYPES = {"edge_pop", "iot_gateway"}
EXPECTED_ZONES = {"zone-a", "zone-b", "zone-c", "zone-d"}
EXPECTED_METRICS = {
    "link_rtt_ms",
    "packet_loss_pct",
    "link_up",
    "cabinet_temp_c",
    "power_draw_w",
}


def test_communications_seed_catalog_imports_and_shape() -> None:
    assert COMMUNICATIONS_UTILITY["kind"] == "communications"
    assert COMMUNICATIONS_UTILITY["id"] == "util-communications-01"
    assert len(COMMUNICATIONS_ASSETS) == 8
    assert {a["id"] for a in COMMUNICATIONS_ASSETS} == set(COMMUNICATIONS_ASSET_IDS)
    assert all(a["utility_kind"] == "communications" for a in COMMUNICATIONS_ASSETS)
    assert {a["zone_id"] for a in COMMUNICATIONS_ASSETS} == EXPECTED_ZONES
    assert {a["type"] for a in COMMUNICATIONS_ASSETS} == EXPECTED_COMM_TYPES
    # One of each type per zone.
    for zone_id in EXPECTED_ZONES:
        in_zone = [a for a in COMMUNICATIONS_ASSETS if a["zone_id"] == zone_id]
        assert len(in_zone) == 2
        assert {a["type"] for a in in_zone} == EXPECTED_COMM_TYPES
    # IDs must not collide with Water/Power/Gas.
    assert set(COMMUNICATIONS_ASSET_IDS).isdisjoint({a["id"] for a in WATER_ASSETS})
    assert set(COMMUNICATIONS_ASSET_IDS).isdisjoint(set(POWER_ASSET_IDS))
    assert set(COMMUNICATIONS_ASSET_IDS).isdisjoint(set(GAS_ASSET_IDS))


def test_communications_seed_persists_assets_and_sensors() -> None:
    seed_all()
    db = SessionLocal()
    try:
        util = db.get(Utility, COMMUNICATIONS_UTILITY["id"])
        assert util is not None
        assert util.kind == "communications"
        assert util.enabled is True

        assets = db.query(Asset).filter(Asset.utility_kind == "communications").all()
        assert len(assets) == 8
        assert {a.id for a in assets} == set(COMMUNICATIONS_ASSET_IDS)
        assert {a.type for a in assets} == EXPECTED_COMM_TYPES
        assert {a.zone_id for a in assets} == EXPECTED_ZONES
        assert all(a.utility_kind == "communications" for a in assets)

        for zone_id in EXPECTED_ZONES:
            in_zone = [a for a in assets if a.zone_id == zone_id]
            assert len(in_zone) == 2
            assert {a.type for a in in_zone} == EXPECTED_COMM_TYPES
            assert all(
                isinstance(a.latitude, float) and isinstance(a.longitude, float)
                for a in in_zone
            )

        sensor_ids: list[str] = []
        sensor_total = 0
        for asset in assets:
            expected = set(ASSET_TYPE_METRICS[asset.type])
            assert expected == EXPECTED_METRICS
            sensors = db.query(Sensor).filter(Sensor.asset_id == asset.id).all()
            keys = {s.metric_key for s in sensors}
            assert keys == EXPECTED_METRICS
            sensor_total += len(sensors)
            for sensor in sensors:
                assert sensor.id == f"SNS-{asset.id}-{sensor.metric_key}"
                assert sensor.unit == COMMUNICATIONS_METRICS[sensor.metric_key]["unit"]
                sensor_ids.append(sensor.id)

        assert sensor_total == 40  # 8 assets × 5 metrics
        assert len(sensor_ids) == len(set(sensor_ids))

        for zone_id in EXPECTED_ZONES:
            zone = db.get(Zone, zone_id)
            assert zone is not None
            assert "communications" in (zone.utility_kinds or [])
    finally:
        db.close()


def test_communications_seed_idempotent_and_other_utilities_intact() -> None:
    first = seed_all()
    second = seed_all()
    assert first["utilities"] == 4
    assert second["utilities"] == 4
    expected_assets = (
        len(WATER_ASSETS) + len(POWER_ASSETS) + len(GAS_ASSETS) + len(COMMUNICATIONS_ASSETS)
    )
    assert first["assets"] == expected_assets
    assert second["assets"] == first["assets"]
    assert first["sensors"] == second["sensors"]

    db = SessionLocal()
    try:
        assert db.query(Asset).filter(Asset.utility_kind == "water").count() == len(
            WATER_ASSETS
        )
        assert db.query(Asset).filter(Asset.utility_kind == "power").count() == len(
            POWER_ASSETS
        )
        assert db.query(Asset).filter(Asset.utility_kind == "gas").count() == len(GAS_ASSETS)
        assert db.query(Asset).filter(Asset.utility_kind == "communications").count() == 8
        assert len(GAS_ASSETS) == 12
        assert {a.id for a in db.query(Asset).filter(Asset.utility_kind == "gas")} == set(
            GAS_ASSET_IDS
        )
    finally:
        db.close()
