"""Tests for zone average_level exposure and per-zone scenario resolution."""

from __future__ import annotations

from app.api.schemas.dashboard import ZoneWaterSummaryOut
from app.main import app
from app.scenarios.definitions import ScenarioKey
from app.scenarios.modifiers import apply_rule, rules_for
from app.scenarios.resolution import (
    DEMO_ZONE_SCENARIO_KEYS,
    normalize_zone_scenarios,
    resolve_effective_scenario,
)


def test_zone_summary_schema_includes_average_level() -> None:
    row = ZoneWaterSummaryOut(
        zone_id="zone-a",
        zone_name="Zone A",
        asset_count=2,
        average_health=90.0,
        average_pressure=3.0,
        average_flow=100.0,
        degraded_assets=0,
        average_level=72.5,
    )
    assert row.average_level == 72.5
    null_row = ZoneWaterSummaryOut(
        zone_id="zone-x",
        zone_name="Zone X",
        asset_count=0,
        average_health=90.0,
        average_level=None,
    )
    assert null_row.average_level is None


def test_resolve_falls_back_to_global_when_no_zone_override() -> None:
    key, intensity = resolve_effective_scenario(
        global_key=ScenarioKey.PEAK_DEMAND.value,
        global_intensity=0.5,
        zone_scenarios={},
        zone_id="zone-a",
    )
    assert key == ScenarioKey.PEAK_DEMAND
    assert intensity == 0.5


def test_resolve_uses_zone_override() -> None:
    zones = {
        "zone-c": {"scenario_key": ScenarioKey.PUMP_FAILURE.value, "intensity": 0.4},
        "zone-d": {"scenario_key": ScenarioKey.POWER_OUTAGE.value, "intensity": 0.9},
    }
    key_a, intensity_a = resolve_effective_scenario(
        global_key=ScenarioKey.NORMAL.value,
        global_intensity=1.0,
        zone_scenarios=zones,
        zone_id="zone-a",
    )
    key_c, intensity_c = resolve_effective_scenario(
        global_key=ScenarioKey.NORMAL.value,
        global_intensity=1.0,
        zone_scenarios=zones,
        zone_id="zone-c",
    )
    key_d, intensity_d = resolve_effective_scenario(
        global_key=ScenarioKey.NORMAL.value,
        global_intensity=1.0,
        zone_scenarios=zones,
        zone_id="zone-d",
    )
    assert key_a == ScenarioKey.NORMAL
    assert intensity_a == 1.0
    assert key_c == ScenarioKey.PUMP_FAILURE
    assert intensity_c == 0.4
    assert key_d == ScenarioKey.POWER_OUTAGE
    assert intensity_d == 0.9


def test_zone_a_does_not_get_zone_d_critical_modifier() -> None:
    """Normal zone leaves pressure unchanged; critical zone applies power_outage rule."""
    zones = {
        "zone-a": {"scenario_key": ScenarioKey.NORMAL.value, "intensity": 1.0},
        "zone-d": {"scenario_key": ScenarioKey.POWER_OUTAGE.value, "intensity": 1.0},
    }
    key_a, intensity_a = resolve_effective_scenario(
        global_key=ScenarioKey.NORMAL.value,
        global_intensity=1.0,
        zone_scenarios=zones,
        zone_id="zone-a",
    )
    key_d, intensity_d = resolve_effective_scenario(
        global_key=ScenarioKey.NORMAL.value,
        global_intensity=1.0,
        zone_scenarios=zones,
        zone_id="zone-d",
    )
    base = 3.0
    # Mimic ScenarioEngine.adjust_metric selection logic
    if key_a == ScenarioKey.NORMAL:
        value_a = base
    else:
        rule = rules_for(key_a, "pump_station").get("pressure")
        value_a = apply_rule(base, rule, intensity=intensity_a) if rule else base

    rule_d = rules_for(key_d, "pump_station").get("pressure")
    assert rule_d is not None
    value_d = apply_rule(base, rule_d, intensity=intensity_d)

    assert value_a == base
    assert value_d < base


def test_demo_asymmetric_keys() -> None:
    assert DEMO_ZONE_SCENARIO_KEYS["zone-a"] == ScenarioKey.NORMAL.value
    assert DEMO_ZONE_SCENARIO_KEYS["zone-b"] == ScenarioKey.NORMAL.value
    assert DEMO_ZONE_SCENARIO_KEYS["zone-c"] == ScenarioKey.PUMP_FAILURE.value
    assert DEMO_ZONE_SCENARIO_KEYS["zone-d"] == ScenarioKey.POWER_OUTAGE.value


def test_pump_failure_drains_storage_levels() -> None:
    """Zone C (pump_failure) must progressively lower tank/reservoir levels."""
    tank_rule = rules_for(ScenarioKey.PUMP_FAILURE, "storage_tank").get("tank_level")
    res_rule = rules_for(ScenarioKey.PUMP_FAILURE, "reservoir").get("reservoir_level")
    assert tank_rule is not None and tank_rule.drift < 0
    assert res_rule is not None and res_rule.drift < 0
    after = apply_rule(70.0, tank_rule, intensity=1.0)
    assert after < 70.0
    assert after >= (tank_rule.floor or 0)


def test_power_outage_drains_storage_faster_than_pump_failure() -> None:
    """Zone D (power_outage) level drift must be stronger than Zone C."""
    c_rule = rules_for(ScenarioKey.PUMP_FAILURE, "storage_tank")["tank_level"]
    d_rule = rules_for(ScenarioKey.POWER_OUTAGE, "storage_tank")["tank_level"]
    assert d_rule.drift < c_rule.drift  # more negative
    c_val = apply_rule(70.0, c_rule, intensity=1.0)
    d_val = apply_rule(70.0, d_rule, intensity=1.0)
    assert d_val < c_val


def test_normal_ops_bounds_levels_in_healthy_band() -> None:
    from app.scenarios.engine import ScenarioEngine

    assert ScenarioEngine._bound_normal_metric("tank_level", 60.0) >= 74.0
    assert ScenarioEngine._bound_normal_metric("tank_level", 95.0) <= 89.0
    assert ScenarioEngine._bound_normal_metric("reservoir_level", 50.0) >= 73.0
    assert ScenarioEngine._bound_normal_metric("pressure", 0.8) >= 2.45
    assert ScenarioEngine._bound_normal_metric("pressure", 3.0) == 3.0


def test_demo_level_seeds_cover_four_zones() -> None:
    from app.scenarios.demo_water_levels import DEMO_ZONE_LEVEL_SEEDS, DEMO_ZONE_PRESSURE_SEEDS

    assert set(DEMO_ZONE_LEVEL_SEEDS) == {"zone-a", "zone-b", "zone-c", "zone-d"}
    assert DEMO_ZONE_LEVEL_SEEDS["zone-a"] >= 75
    assert DEMO_ZONE_LEVEL_SEEDS["zone-b"] >= 75
    assert DEMO_ZONE_LEVEL_SEEDS["zone-c"] < 70
    assert DEMO_ZONE_LEVEL_SEEDS["zone-d"] < 40
    assert DEMO_ZONE_LEVEL_SEEDS["zone-d"] < DEMO_ZONE_LEVEL_SEEDS["zone-c"]
    assert DEMO_ZONE_PRESSURE_SEEDS["zone-a"] >= 2.5
    assert DEMO_ZONE_PRESSURE_SEEDS["zone-b"] >= 2.5


def test_normalize_rejects_invalid_keys() -> None:
    cleaned = normalize_zone_scenarios(
        {
            "zone-a": {"scenario_key": "not_a_real_scenario", "intensity": 1},
            "zone-b": {"scenario_key": ScenarioKey.PIPELINE_LEAK.value, "intensity": 2.5},
        }
    )
    assert "zone-a" not in cleaned
    assert cleaned["zone-b"]["scenario_key"] == ScenarioKey.PIPELINE_LEAK.value
    assert cleaned["zone-b"]["intensity"] == 1.0  # clamped


def test_scenario_zone_routes_registered() -> None:
    paths = sorted(app.openapi()["paths"].keys())
    assert "/api/scenarios/current" in paths
    assert "/api/scenarios/zones" in paths
    assert "/api/scenarios/zones/demo-asymmetric" in paths
    assert "/api/dashboard/water" in paths
