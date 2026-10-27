"""Phase 17.1 — Communications plugin registration + metric catalog foundation."""

from __future__ import annotations

from app.domain.enums import UtilityKind
from app.utilities.communications.metrics import (
    ASSET_TYPE_METRICS,
    COMMUNICATIONS_METRICS,
    PROFILE_FIELD_BY_METRIC,
    catalog,
)
from app.utilities.communications.plugin import CommunicationsPlugin
from app.utilities.gas.plugin import GasPlugin
from app.utilities.power.plugin import PowerPlugin
from app.utilities.registry import UtilityRegistry, bootstrap_plugins, registry
from app.utilities.water.plugin import WaterPlugin

EXPECTED_COMM_TYPES = {"edge_pop", "iot_gateway"}
EXPECTED_COMM_METRICS = {
    "link_rtt_ms",
    "packet_loss_pct",
    "link_up",
    "cabinet_temp_c",
    "power_draw_w",
}
FUTURE_KINDS = {
    "road",
    "buildings",
    "airport",
    "rail",
    "port",
    "renewables",
}


def test_communications_plugin_imports_and_kind() -> None:
    plugin = CommunicationsPlugin()
    assert plugin.kind == UtilityKind.COMMUNICATIONS.value
    assert plugin.kind == "communications"
    assert plugin.display_name == "Communications"
    assert set(plugin.asset_types()) == EXPECTED_COMM_TYPES


def test_communications_metric_catalog_exact_five() -> None:
    rows = catalog()
    keys = [row["key"] for row in rows]
    assert len(keys) == 5
    assert len(set(keys)) == 5
    assert set(keys) == EXPECTED_COMM_METRICS
    assert set(COMMUNICATIONS_METRICS.keys()) == EXPECTED_COMM_METRICS

    by_key = {row["key"]: row for row in rows}
    assert by_key["link_rtt_ms"]["unit"] == "ms"
    assert by_key["packet_loss_pct"]["unit"] == "%"
    assert by_key["link_up"]["unit"] == "0-1"
    assert by_key["cabinet_temp_c"]["unit"] == "°C"
    assert by_key["power_draw_w"]["unit"] == "W"

    assert set(ASSET_TYPE_METRICS.keys()) == EXPECTED_COMM_TYPES
    for asset_type, metric_keys in ASSET_TYPE_METRICS.items():
        assert set(metric_keys) == EXPECTED_COMM_METRICS, asset_type
    assert set(PROFILE_FIELD_BY_METRIC.keys()) == EXPECTED_COMM_METRICS


def test_bootstrap_registers_water_power_gas_communications() -> None:
    bootstrap_plugins(["water", "power", "gas", "communications"])
    assert set(registry.kinds()) >= {"water", "power", "gas", "communications"}
    plugin = registry.get("communications")
    assert isinstance(plugin, CommunicationsPlugin)
    keys = {row["key"] for row in plugin.metric_catalog()}
    assert keys == EXPECTED_COMM_METRICS


def test_future_kinds_remain_unregistered() -> None:
    local = UtilityRegistry()
    # Mirror bootstrap available map without enabling future domains.
    local.register(WaterPlugin())
    local.register(PowerPlugin())
    local.register(GasPlugin())
    local.register(CommunicationsPlugin())
    assert set(local.kinds()) == {"communications", "gas", "power", "water"}
    for kind in FUTURE_KINDS:
        assert kind not in local.kinds()


def test_existing_water_power_gas_plugins_still_import() -> None:
    assert WaterPlugin().kind == UtilityKind.WATER.value
    assert PowerPlugin().kind == UtilityKind.POWER.value
    assert GasPlugin().kind == UtilityKind.GAS.value
    assert set(WaterPlugin().asset_types())
    assert set(PowerPlugin().asset_types())
    assert set(GasPlugin().asset_types())
