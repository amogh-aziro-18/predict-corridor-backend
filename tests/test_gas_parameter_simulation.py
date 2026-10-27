"""Gas parameter what-if — Digital Twin calculate support using Gas health bands."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db.seed import seed_all
from app.main import app
from app.services.parameter_simulation import (
    GAS_PARAMETERS,
    list_supported_parameters,
    project_parameter,
)
from app.utilities.gas import health as gas_health


def test_gas_parameter_catalog() -> None:
    keys = {p["parameter"] for p in list_supported_parameters("gas", set(GAS_PARAMETERS))}
    assert keys == set(GAS_PARAMETERS)
    assert list_supported_parameters("gas", set())[0]["available"] is False


def test_gas_load_projection_math_and_bands() -> None:
    # 70 * 1.15 = 80.5 → between LOAD_WARN (75) and LOAD_HIGH (88) → STABLE
    warn = project_parameter(
        utility_kind="gas",
        asset_type="gas_compressor",
        parameter="compressor_load_pct",
        baseline_value=70.0,
        delta_percent=15.0,
        absolute_value=None,
    )
    assert warn.available
    assert abs(warn.projected_value - 80.5) < 0.01
    assert warn.condition == "STABLE"

    # 92 * 1.10 = 101.2 → ≥ LOAD_CRITICAL (98) → CRITICAL
    critical = project_parameter(
        utility_kind="gas",
        asset_type="gas_compressor",
        parameter="compressor_load_pct",
        baseline_value=92.0,
        delta_percent=10.0,
        absolute_value=None,
    )
    assert critical.condition == "CRITICAL"
    assert critical.potential_alert is True
    assert str(gas_health.LOAD_CRITICAL) in critical.explanation or any(
        "LOAD_CRITICAL" in r for r in critical.rule_refs
    )


def test_gas_pressure_relative_deviation_math() -> None:
    # nominal=28, projected=28*1.20=33.6 → |Δ|/n = 0.20 → HIGH (0.18) → AT RISK
    proj = project_parameter(
        utility_kind="gas",
        asset_type="gas_compressor",
        parameter="pressure_bar",
        baseline_value=28.0,
        delta_percent=20.0,
        absolute_value=None,
        nominal_value=28.0,
    )
    assert proj.available
    assert abs(proj.projected_value - 33.6) < 0.01
    assert proj.condition == "AT RISK"
    assert any("REL_DEV_HIGH" in r for r in proj.rule_refs)

    # 28% deviation → CRITICAL
    crit = project_parameter(
        utility_kind="gas",
        asset_type="gas_feeder",
        parameter="pressure_bar",
        baseline_value=28.0,
        delta_percent=30.0,
        absolute_value=None,
        nominal_value=28.0,
    )
    assert abs(crit.projected_value - 36.4) < 0.01
    # |36.4-28|/28 = 0.3 ≥ 0.28
    assert crit.condition == "CRITICAL"


def test_gas_quality_lower_is_worse() -> None:
    proj = project_parameter(
        utility_kind="gas",
        asset_type="gas_feeder",
        parameter="gas_quality_pct",
        baseline_value=96.0,
        delta_percent=-15.0,
        absolute_value=None,
    )
    # 96 * 0.85 = 81.6 ≤ QUALITY_CRITICAL (85)
    assert abs(proj.projected_value - 81.6) < 0.01
    assert proj.condition == "CRITICAL"


def test_gas_unsupported_utility_still_rejected_for_others() -> None:
    proj = project_parameter(
        utility_kind="telecom",
        asset_type="tower",
        parameter="pressure_bar",
        baseline_value=10.0,
        delta_percent=5.0,
        absolute_value=None,
    )
    assert proj.available is False


def test_gas_parameters_endpoint_and_what_if_api() -> None:
    seed_all()
    client = TestClient(app)
    assets = client.get("/api/assets", params={"utility": "gas"}).json()
    assert len(assets) == 12
    # Prefer compressor so compressor_load_pct is in catalog when telemetry exists.
    cmp = next(a for a in assets if a["id"].startswith("CMP-"))
    params = client.get(f"/api/simulations/parameters/{cmp['id']}")
    assert params.status_code == 200, params.text
    catalog = params.json()["parameters"]
    names = {p["parameter"] for p in catalog}
    assert "compressor_load_pct" in names
    assert "pressure_bar" in names
    assert "Water/Power/Gas" in params.json()["disclaimer"] or "Gas" in params.json()["disclaimer"]

    # Water/Power catalogs must remain intact.
    water = client.get("/api/assets", params={"utility": "water"}).json()[0]
    water_params = client.get(f"/api/simulations/parameters/{water['id']}").json()["parameters"]
    assert {p["parameter"] for p in water_params} == {
        "pressure",
        "tank_level",
        "reservoir_level",
        "flow_rate",
        "pump_speed",
    }

    # If no live Gas telemetry, Calculate returns honest unavailable (still 200).
    load = next(p for p in catalog if p["parameter"] == "compressor_load_pct")
    run = client.post(
        "/api/simulations/parameter-what-if",
        json={
            "asset_id": cmp["id"],
            "parameter": "compressor_load_pct",
            "delta_percent": 10,
        },
    )
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["physics_based"] is False
    if not load["available"]:
        assert body["available"] is False
        assert "unavailable" in body["explanation"].lower()
    else:
        assert body["available"] is True
        assert body["baseline_value"] is not None
        assert body["projected_value"] is not None
        assert abs(body["projected_value"] - body["baseline_value"] * 1.1) < 0.05
        assert body["explanation"]
        assert body["rule_refs"]
