"""Communications parameter what-if — Digital Twin using Communications health bands."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.schemas.telemetry import TelemetryEnvelope
from app.db.seed import seed_all
from app.db.session import SessionLocal
from app.main import app
from app.services.parameter_simulation import (
    COMMUNICATIONS_PARAMETERS,
    list_supported_parameters,
    project_parameter,
)
from app.services.telemetry_service import TelemetryService
from app.utilities.communications import health as communications_health
from app.utilities.communications.seed_catalog import COMMUNICATIONS_ASSET_IDS


def _ensure_comm_metric(
    db,
    asset_id: str,
    metric_key: str,
    value: float,
    *,
    unit: str = "",
) -> None:
    TelemetryService(db).persist_envelope(
        asset_id=asset_id,
        metric_key=metric_key,
        envelope=TelemetryEnvelope(
            value=value,
            unit=unit,
            quality="good",
            metric_key=metric_key,
        ),
        utility_kind="communications",
        evaluate_power=False,
        evaluate_gas=False,
        evaluate_communications=False,
    )


def test_communications_parameter_catalog() -> None:
    keys = {
        p["parameter"]
        for p in list_supported_parameters("communications", set(COMMUNICATIONS_PARAMETERS))
    }
    assert keys == set(COMMUNICATIONS_PARAMETERS)
    assert list_supported_parameters("communications", set())[0]["available"] is False


def test_communications_rtt_projection_bands() -> None:
    # nominal=12, projected=12+8=20 → delta 8 → warning
    warn = project_parameter(
        utility_kind="communications",
        asset_type="edge_pop",
        parameter="link_rtt_ms",
        baseline_value=12.0,
        delta_percent=None,
        absolute_value=12.0 + communications_health.RTT_DELTA_WARN,
        nominal_value=12.0,
    )
    assert warn.available
    assert warn.condition == "warning"
    assert any("RTT_DELTA_WARN" in r for r in warn.rule_refs)

    # delta 20 → high_risk
    high = project_parameter(
        utility_kind="communications",
        asset_type="edge_pop",
        parameter="link_rtt_ms",
        baseline_value=12.0,
        delta_percent=None,
        absolute_value=12.0 + communications_health.RTT_DELTA_HIGH,
        nominal_value=12.0,
    )
    assert high.condition == "high_risk"

    # delta 40 → critical
    crit = project_parameter(
        utility_kind="communications",
        asset_type="edge_pop",
        parameter="link_rtt_ms",
        baseline_value=12.0,
        delta_percent=None,
        absolute_value=12.0 + communications_health.RTT_DELTA_CRITICAL,
        nominal_value=12.0,
    )
    assert crit.condition == "critical"
    assert crit.potential_alert is True


def test_communications_packet_loss_projection() -> None:
    proj = project_parameter(
        utility_kind="communications",
        asset_type="iot_gateway",
        parameter="packet_loss_pct",
        baseline_value=0.2,
        delta_percent=None,
        absolute_value=communications_health.LOSS_CRITICAL,
    )
    assert proj.condition == "critical"


def test_communications_link_down_dominates() -> None:
    down = project_parameter(
        utility_kind="communications",
        asset_type="edge_pop",
        parameter="link_up",
        baseline_value=1.0,
        delta_percent=-100.0,
        absolute_value=None,
    )
    assert down.available
    assert abs(down.projected_value - 0.0) < 0.01
    assert down.condition == "unavailable"
    assert any("LINK_DOWN" in r for r in down.rule_refs)

    up = project_parameter(
        utility_kind="communications",
        asset_type="edge_pop",
        parameter="link_up",
        baseline_value=1.0,
        delta_percent=None,
        absolute_value=1.0,
    )
    assert up.condition == "healthy"


def test_communications_cabinet_temp_and_power_draw() -> None:
    temp = project_parameter(
        utility_kind="communications",
        asset_type="edge_pop",
        parameter="cabinet_temp_c",
        baseline_value=28.0,
        delta_percent=None,
        absolute_value=communications_health.TEMP_HIGH,
    )
    assert temp.condition == "high_risk"

    # 20% over nominal → ≥ POWER_REL_HIGH (0.18) → high_risk (power_draw_high)
    power = project_parameter(
        utility_kind="communications",
        asset_type="edge_pop",
        parameter="power_draw_w",
        baseline_value=180.0,
        delta_percent=20.0,
        absolute_value=None,
        nominal_value=180.0,
    )
    assert abs(power.projected_value - 216.0) < 0.01
    assert power.condition == "high_risk"


def test_communications_unsupported_utility_still_rejected() -> None:
    proj = project_parameter(
        utility_kind="telecom",
        asset_type="tower",
        parameter="link_rtt_ms",
        baseline_value=10.0,
        delta_percent=5.0,
        absolute_value=None,
    )
    assert proj.available is False


def test_communications_parameters_endpoint_baseline_and_what_if_api() -> None:
    seed_all()
    db = SessionLocal()
    try:
        _ensure_comm_metric(db, "POP-A-01", "link_rtt_ms", 12.0, unit="ms")
        _ensure_comm_metric(db, "POP-A-01", "packet_loss_pct", 0.2, unit="%")
        _ensure_comm_metric(db, "POP-A-01", "link_up", 1.0, unit="0/1")
        _ensure_comm_metric(db, "POP-A-01", "cabinet_temp_c", 28.0, unit="°C")
        _ensure_comm_metric(db, "POP-A-01", "power_draw_w", 180.0, unit="W")
    finally:
        db.close()

    client = TestClient(app)
    assets = client.get("/api/assets", params={"utility": "communications"}).json()
    assert len(assets) == len(COMMUNICATIONS_ASSET_IDS)
    pop = next(a for a in assets if a["id"] == "POP-A-01")

    params = client.get(f"/api/simulations/parameters/{pop['id']}")
    assert params.status_code == 200, params.text
    body = params.json()
    names = {p["parameter"] for p in body["parameters"]}
    assert names == set(COMMUNICATIONS_PARAMETERS)
    assert all(p["available"] for p in body["parameters"])
    assert "Communications" in body["disclaimer"]

    # Water/Power/Gas catalogs remain intact.
    water = client.get("/api/assets", params={"utility": "water"}).json()[0]
    water_params = client.get(f"/api/simulations/parameters/{water['id']}").json()["parameters"]
    assert {p["parameter"] for p in water_params} == {
        "pressure",
        "tank_level",
        "reservoir_level",
        "flow_rate",
        "pump_speed",
    }
    gas = client.get("/api/assets", params={"utility": "gas"}).json()[0]
    gas_names = {
        p["parameter"]
        for p in client.get(f"/api/simulations/parameters/{gas['id']}").json()["parameters"]
    }
    assert "pressure_bar" in gas_names or "compressor_load_pct" in gas_names

    baseline = client.get(f"/api/simulations/baseline/{pop['id']}")
    assert baseline.status_code == 200, baseline.text
    bl = baseline.json()
    assert bl["utility"] == "communications"
    assert bl["asset_id"] == "POP-A-01"
    metric_keys = {m["metric_key"] for m in bl["metrics"]}
    assert "link_rtt_ms" in metric_keys
    assert bl["metrics"]  # live baseline — not fabricated empty

    run = client.post(
        "/api/simulations/parameter-what-if",
        json={
            "asset_id": "POP-A-01",
            "parameter": "link_rtt_ms",
            "absolute_value": 12.0 + communications_health.RTT_DELTA_CRITICAL,
        },
    )
    assert run.status_code == 200, run.text
    result = run.json()
    assert result["physics_based"] is False
    assert result["available"] is True
    assert result["projected_condition"] == "critical"
    assert result["baseline_value"] is not None
    assert "dependencies" in result
    dep_ids = {d["asset_id"] for d in (result["dependencies"].get("depends_on") or [])}
    assert "FDR-A-01" in dep_ids
    # POP has no outgoing supplies — CRITICAL impact assessed empty / none claimed as outage.
    impact = result.get("dependency_impact")
    if impact is not None:
        assert impact.get("hypothetical") is True
        assert impact.get("mutates_operational_state") is False

    down = client.post(
        "/api/simulations/parameter-what-if",
        json={
            "asset_id": "POP-A-01",
            "parameter": "link_up",
            "delta_percent": -100,
        },
    )
    assert down.status_code == 200, down.text
    down_body = down.json()
    assert down_body["available"] is True
    assert down_body["projected_condition"] == "unavailable"


def test_water_power_gas_projection_regression() -> None:
    """Existing W/P/G projectors still return DT operator vocabulary."""
    water = project_parameter(
        utility_kind="water",
        asset_type="pump",
        parameter="pressure",
        baseline_value=3.0,
        delta_percent=None,
        absolute_value=1.0,
    )
    assert water.available
    assert water.condition in {"HEALTHY", "STABLE", "AT RISK", "CRITICAL"}

    power = project_parameter(
        utility_kind="power",
        asset_type="transformer",
        parameter="load_pct",
        baseline_value=70.0,
        delta_percent=10.0,
        absolute_value=None,
    )
    assert power.available
    assert power.condition in {"HEALTHY", "STABLE", "AT RISK", "CRITICAL"}

    gas = project_parameter(
        utility_kind="gas",
        asset_type="gas_compressor",
        parameter="compressor_load_pct",
        baseline_value=70.0,
        delta_percent=15.0,
        absolute_value=None,
    )
    assert gas.available
    assert gas.condition in {"HEALTHY", "STABLE", "AT RISK", "CRITICAL"}
