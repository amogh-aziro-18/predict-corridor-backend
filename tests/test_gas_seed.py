"""Phase 15.0 — Gas plugin registration + PostgreSQL seed foundation."""

from __future__ import annotations

from app.db.seed import seed_all
from app.db.session import SessionLocal
from app.domain.enums import UtilityKind
from app.domain.models import Asset, Sensor, Utility, Zone
from app.utilities.gas.metrics import ASSET_TYPE_METRICS, GAS_METRICS, catalog
from app.utilities.gas.plugin import GasPlugin
from app.utilities.gas.seed_catalog import GAS_ASSET_IDS, GAS_ASSETS, GAS_UTILITY
from app.utilities.power.seed_catalog import POWER_ASSETS, POWER_ASSET_IDS
from app.utilities.registry import bootstrap_plugins, registry
from app.utilities.water.seed_catalog import WATER_ASSETS


EXPECTED_GAS_TYPES = {"gas_compressor", "gas_regulator_station", "gas_feeder"}
EXPECTED_ZONES = {"zone-a", "zone-b", "zone-c", "zone-d"}
# Gas-specific metric keys — must not collide with Water/Power catalogs.
WATER_POWER_METRIC_KEYS = {
    "flow_rate",
    "pressure",
    "tank_level",
    "reservoir_level",
    "inflow",
    "outflow",
    "pump_speed",
    "pump_running",
    "chlorine_level",
    "turbidity",
    "valve_position",
    "voltage_kv",
    "current_a",
    "active_power_mw",
    "load_pct",
    "frequency_hz",
    "oil_temp_c",
    "power_factor",
}


def test_gas_plugin_registers_alongside_water_and_power() -> None:
    bootstrap_plugins(["water", "power", "gas"])
    assert "water" in registry.kinds()
    assert "power" in registry.kinds()
    assert "gas" in registry.kinds()

    plugin = registry.get("gas")
    assert isinstance(plugin, GasPlugin)
    assert plugin.kind == UtilityKind.GAS.value
    assert plugin.display_name == "Gas"
    assert set(plugin.asset_types()) == EXPECTED_GAS_TYPES
    keys = {row["key"] for row in plugin.metric_catalog()}
    assert keys == set(GAS_METRICS.keys())


def test_gas_not_registered_when_only_water_power_enabled() -> None:
    # Fresh process registry may already have gas from prior tests; bootstrap is additive.
    # Verify skipping: unknown/disabled kinds alone don't invent Gas when not requested
    # after a clean registration of water+power only on a new registry instance.
    from app.utilities.registry import UtilityRegistry
    from app.utilities.power.plugin import PowerPlugin
    from app.utilities.water.plugin import WaterPlugin

    local = UtilityRegistry()
    local.register(WaterPlugin())
    local.register(PowerPlugin())
    assert "gas" not in local.kinds()


def test_gas_metric_catalog_units_and_isolation() -> None:
    rows = catalog()
    assert len(rows) == 10
    by_key = {row["key"]: row for row in rows}
    assert by_key["pressure_bar"]["unit"] == "bar"
    assert by_key["flow_rate_nm3_h"]["unit"] == "Nm³/h"
    assert by_key["compressor_load_pct"]["unit"] == "%"
    assert by_key["leak_indicator"]["unit"] == "0-1"
    assert set(ASSET_TYPE_METRICS.keys()) == EXPECTED_GAS_TYPES
    assert set(GAS_METRICS.keys()).isdisjoint(WATER_POWER_METRIC_KEYS)


def test_gas_seed_catalog_shape() -> None:
    assert GAS_UTILITY["id"] == "util-gas-01"
    assert GAS_UTILITY["kind"] == "gas"
    assert len(GAS_ASSETS) == 12
    assert {a["id"] for a in GAS_ASSETS} == set(GAS_ASSET_IDS)
    assert all(a["utility_kind"] == "gas" for a in GAS_ASSETS)
    assert {a["zone_id"] for a in GAS_ASSETS} == EXPECTED_ZONES
    assert {a["type"] for a in GAS_ASSETS} == EXPECTED_GAS_TYPES
    assert set(GAS_ASSET_IDS).isdisjoint({a["id"] for a in WATER_ASSETS})
    assert set(GAS_ASSET_IDS).isdisjoint(set(POWER_ASSET_IDS))


def test_gas_seed_persists_utility_assets_sensors_and_zones() -> None:
    seed_all()
    db = SessionLocal()
    try:
        util = db.get(Utility, GAS_UTILITY["id"])
        assert util is not None
        assert util.kind == "gas"
        assert util.enabled is True

        gas_assets = db.query(Asset).filter(Asset.utility_kind == "gas").all()
        assert len(gas_assets) == 12
        assert {a.id for a in gas_assets} == set(GAS_ASSET_IDS)
        assert all(a.utility_kind == "gas" for a in gas_assets)
        assert {a.zone_id for a in gas_assets} == EXPECTED_ZONES
        assert {a.type for a in gas_assets} == EXPECTED_GAS_TYPES
        # Exactly three Gas assets per zone (CMP + REG + GFDR).
        for zone_id in EXPECTED_ZONES:
            in_zone = [a for a in gas_assets if a.zone_id == zone_id]
            assert len(in_zone) == 3
            assert {a.type for a in in_zone} == EXPECTED_GAS_TYPES
            assert all(
                isinstance(a.latitude, float) and isinstance(a.longitude, float)
                for a in in_zone
            )

        sensor_total = 0
        for asset in gas_assets:
            expected_metrics = ASSET_TYPE_METRICS[asset.type]
            sensors = db.query(Sensor).filter(Sensor.asset_id == asset.id).all()
            keys = {s.metric_key for s in sensors}
            assert keys == set(expected_metrics)
            sensor_total += len(sensors)
            for sensor in sensors:
                assert sensor.id == f"SNS-{asset.id}-{sensor.metric_key}"
                assert sensor.unit == GAS_METRICS[sensor.metric_key]["unit"]
                assert sensor.metric_key not in WATER_POWER_METRIC_KEYS

        assert sensor_total == sum(len(ASSET_TYPE_METRICS[a.type]) for a in gas_assets)

        for zone_id in EXPECTED_ZONES:
            zone = db.get(Zone, zone_id)
            assert zone is not None
            assert "water" in zone.utility_kinds
            assert "power" in zone.utility_kinds
            assert "gas" in zone.utility_kinds
            assert "communications" in zone.utility_kinds
    finally:
        db.close()


def test_gas_utility_api_lists_all_four_zones() -> None:
    """GET /utilities must expose Gas across Zones A–D (parity with Water/Power)."""
    from fastapi.testclient import TestClient

    from app.main import app

    seed_all()
    client = TestClient(app)
    resp = client.get("/api/utilities")
    assert resp.status_code == 200, resp.text
    gas = next(u for u in resp.json() if u["kind"] == "gas")
    assert set(gas["zones"]) == EXPECTED_ZONES
    assert gas["assets"] == 12

    assets = client.get("/api/assets", params={"utility": "gas"}).json()
    assert len(assets) == 12
    assert {a["zone"] for a in assets} == EXPECTED_ZONES

    gis = client.get("/api/gis/layers", params={"utility": "gas"}).json()
    gas_features = gis["assets"]["features"]
    assert len(gas_features) == 12
    assert {f["properties"]["zone_id"] for f in gas_features} == EXPECTED_ZONES
    for feat in gas_features:
        coords = feat["geometry"]["coordinates"]
        assert len(coords) == 2
        assert all(isinstance(c, (int, float)) for c in coords)

def test_gas_seed_idempotent_no_duplicates() -> None:
    from app.utilities.communications.seed_catalog import COMMUNICATIONS_ASSETS

    first = seed_all()
    second = seed_all()

    assert first["utilities"] == 4
    assert second["utilities"] == 4
    expected_assets = (
        len(WATER_ASSETS)
        + len(POWER_ASSETS)
        + len(GAS_ASSETS)
        + len(COMMUNICATIONS_ASSETS)
    )
    assert first["assets"] == expected_assets
    assert second["assets"] == first["assets"]
    assert first["sensors"] == second["sensors"]

    db = SessionLocal()
    try:
        gas_utils = db.query(Utility).filter(Utility.kind == "gas").all()
        assert len(gas_utils) == 1
        assert gas_utils[0].id == GAS_UTILITY["id"]

        gas_assets = db.query(Asset).filter(Asset.utility_kind == "gas").all()
        assert len(gas_assets) == 12

        water_assets = db.query(Asset).filter(Asset.utility_kind == "water").all()
        power_assets = db.query(Asset).filter(Asset.utility_kind == "power").all()
        assert len(water_assets) == len(WATER_ASSETS)
        assert len(power_assets) == len(POWER_ASSETS)
    finally:
        db.close()
