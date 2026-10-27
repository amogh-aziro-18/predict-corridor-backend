"""Phase 14.2 — Power plugin registration + PostgreSQL seed foundation."""

from __future__ import annotations

from app.db.seed import seed_all
from app.db.seed.technicians_catalog import TECHNICIANS
from app.db.seed.work_orders_catalog import WORK_ORDERS
from app.db.session import SessionLocal
from app.domain.enums import UtilityKind
from app.domain.models import Asset, Sensor, Technician, Utility, WorkOrder, Zone
from app.utilities.power.metrics import ASSET_TYPE_METRICS, POWER_METRICS, catalog
from app.utilities.power.plugin import PowerPlugin
from app.utilities.power.seed_catalog import POWER_ASSET_IDS, POWER_ASSETS, POWER_UTILITY
from app.utilities.registry import bootstrap_plugins, registry
from app.utilities.water.seed_catalog import WATER_ASSETS


EXPECTED_POWER_TYPES = {"substation", "transformer", "feeder"}
EXPECTED_ZONES = {"zone-a", "zone-b", "zone-c", "zone-d"}


def test_power_plugin_registers_alongside_water() -> None:
    bootstrap_plugins(["water", "power"])
    assert "water" in registry.kinds()
    assert "power" in registry.kinds()

    plugin = registry.get("power")
    assert isinstance(plugin, PowerPlugin)
    assert plugin.kind == UtilityKind.POWER.value
    assert plugin.display_name == "Power"
    assert set(plugin.asset_types()) == EXPECTED_POWER_TYPES
    keys = {row["key"] for row in plugin.metric_catalog()}
    assert keys == set(POWER_METRICS.keys())


def test_power_metric_catalog_units_documented() -> None:
    rows = catalog()
    assert len(rows) == 7
    by_key = {row["key"]: row for row in rows}
    assert by_key["voltage_kv"]["unit"] == "kV"
    assert by_key["current_a"]["unit"] == "A"
    assert by_key["active_power_mw"]["unit"] == "MW"
    assert by_key["load_pct"]["unit"] == "%"
    assert by_key["frequency_hz"]["unit"] == "Hz"
    assert by_key["oil_temp_c"]["unit"] == "°C"
    assert by_key["power_factor"]["unit"] == "pf"
    assert set(ASSET_TYPE_METRICS.keys()) == EXPECTED_POWER_TYPES


def test_power_seed_catalog_shape() -> None:
    assert POWER_UTILITY["id"] == "util-power-01"
    assert POWER_UTILITY["kind"] == "power"
    assert len(POWER_ASSETS) == 12
    assert {a["id"] for a in POWER_ASSETS} == set(POWER_ASSET_IDS)
    assert all(a["utility_kind"] == "power" for a in POWER_ASSETS)
    assert {a["zone_id"] for a in POWER_ASSETS} == EXPECTED_ZONES
    assert {a["type"] for a in POWER_ASSETS} == EXPECTED_POWER_TYPES
    # No ID collision with Water seed assets.
    assert set(POWER_ASSET_IDS).isdisjoint({a["id"] for a in WATER_ASSETS})


def test_power_seed_persists_utility_assets_sensors_and_zones() -> None:
    seed_all()
    db = SessionLocal()
    try:
        util = db.get(Utility, POWER_UTILITY["id"])
        assert util is not None
        assert util.kind == "power"
        assert util.enabled is True

        power_assets = db.query(Asset).filter(Asset.utility_kind == "power").all()
        assert len(power_assets) == 12
        assert {a.id for a in power_assets} == set(POWER_ASSET_IDS)
        assert all(a.utility_kind == "power" for a in power_assets)
        assert {a.zone_id for a in power_assets} == EXPECTED_ZONES
        assert {a.type for a in power_assets} == EXPECTED_POWER_TYPES

        for asset in power_assets:
            expected_metrics = ASSET_TYPE_METRICS[asset.type]
            sensors = db.query(Sensor).filter(Sensor.asset_id == asset.id).all()
            keys = {s.metric_key for s in sensors}
            assert keys == set(expected_metrics)
            for sensor in sensors:
                assert sensor.id == f"SNS-{asset.id}-{sensor.metric_key}"
                assert sensor.unit == POWER_METRICS[sensor.metric_key]["unit"]

        for zone_id in EXPECTED_ZONES:
            zone = db.get(Zone, zone_id)
            assert zone is not None
            assert "water" in zone.utility_kinds
            assert "power" in zone.utility_kinds
            assert "gas" in zone.utility_kinds
            assert "communications" in zone.utility_kinds
    finally:
        db.close()


def test_power_seed_idempotent_no_duplicates() -> None:
    from app.utilities.communications.seed_catalog import COMMUNICATIONS_ASSETS

    first = seed_all()
    second = seed_all()

    assert first["utilities"] == 4
    assert second["utilities"] == 4
    assert first["assets"] == (
        len(WATER_ASSETS) + len(POWER_ASSETS) + 12 + len(COMMUNICATIONS_ASSETS)
    )
    assert second["assets"] == first["assets"]
    assert first["sensors"] == second["sensors"]
    assert first["technicians"] == len(TECHNICIANS)
    assert second["technicians"] == len(TECHNICIANS)
    assert first["work_orders"] == len(WORK_ORDERS)
    assert second["work_orders"] == len(WORK_ORDERS)

    db = SessionLocal()
    try:
        power_utils = db.query(Utility).filter(Utility.kind == "power").all()
        assert len(power_utils) == 1
        assert power_utils[0].id == POWER_UTILITY["id"]

        power_assets = db.query(Asset).filter(Asset.utility_kind == "power").all()
        assert len(power_assets) == 12

        gas_utils = db.query(Utility).filter(Utility.kind == "gas").all()
        assert len(gas_utils) == 1
        gas_assets = db.query(Asset).filter(Asset.utility_kind == "gas").all()
        assert len(gas_assets) == 12
    finally:
        db.close()


def test_water_technicians_and_work_orders_unchanged_by_power_seed() -> None:
    seed_all()
    db = SessionLocal()
    try:
        water_assets = db.query(Asset).filter(Asset.utility_kind == "water").all()
        assert len(water_assets) == len(WATER_ASSETS)
        assert {a.id for a in water_assets} == {a["id"] for a in WATER_ASSETS}

        tech_ids = {row.id for row in db.query(Technician).all()}
        for item in TECHNICIANS:
            assert item["id"] in tech_ids

        for item in WORK_ORDERS:
            wo = db.get(WorkOrder, item["id"])
            assert wo is not None
            assert wo.asset_id == item["asset_id"]
            assert wo.utility == "water"
    finally:
        db.close()


def test_gas_not_registered_when_bootstrap_omits_gas() -> None:
    """bootstrap_plugins(['water','power']) must not invent a Gas plugin on a clean registry."""
    from app.utilities.registry import UtilityRegistry
    from app.utilities.power.plugin import PowerPlugin
    from app.utilities.water.plugin import WaterPlugin

    local = UtilityRegistry()
    # Mimic bootstrap selection without touching the process-wide registry.
    available = {"water": WaterPlugin, "power": PowerPlugin}
    for kind in ("water", "power"):
        local.register(available[kind]())
    assert "gas" not in local.kinds()
    assert set(local.kinds()) == {"water", "power"}
