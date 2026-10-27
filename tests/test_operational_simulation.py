"""Isolated operational what-if simulation tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.scenarios.definitions import ScenarioKey


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _pick_pump(client: TestClient) -> dict:
    pumps = client.get(
        "/api/assets",
        params={"utility": "water", "asset_type": "pump_station"},
    ).json()
    if not pumps:
        pytest.skip("No pump stations seeded")
    return pumps[0]


def test_openapi_includes_simulation_routes() -> None:
    paths = app.openapi()["paths"]
    assert "/api/simulations/what-if" in paths
    assert "/api/simulations/scenarios" in paths
    assert "/api/simulations/baseline/{asset_id}" in paths


def test_list_simulation_scenarios(client: TestClient) -> None:
    response = client.get("/api/simulations/scenarios")
    assert response.status_code == 200
    rows = response.json()
    keys = {row["key"] for row in rows}
    assert ScenarioKey.PUMP_FAILURE.value in keys
    assert ScenarioKey.POWER_OUTAGE.value in keys
    assert ScenarioKey.NORMAL.value in keys


def test_unsupported_scenario_rejected(client: TestClient) -> None:
    pump = _pick_pump(client)
    response = client.post(
        "/api/simulations/what-if",
        json={
            "asset_id": pump["id"],
            "scenario_key": "not_a_real_scenario",
            "horizon_hours": 24,
        },
    )
    assert response.status_code == 422


def test_unsupported_horizon_rejected(client: TestClient) -> None:
    pump = _pick_pump(client)
    response = client.post(
        "/api/simulations/what-if",
        json={
            "asset_id": pump["id"],
            "scenario_key": ScenarioKey.PUMP_FAILURE.value,
            "horizon_hours": 7,
        },
    )
    assert response.status_code == 422


def test_missing_asset_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/simulations/what-if",
        json={
            "asset_id": "DOES-NOT-EXIST-XYZ",
            "scenario_key": ScenarioKey.PUMP_FAILURE.value,
            "horizon_hours": 24,
        },
    )
    assert response.status_code == 404


def test_what_if_schema_and_asset_consistency(client: TestClient) -> None:
    pump = _pick_pump(client)
    response = client.post(
        "/api/simulations/what-if",
        json={
            "asset_id": pump["id"],
            "scenario_key": ScenarioKey.PUMP_FAILURE.value,
            "horizon_hours": 24,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["physics_based"] is False
    assert body["mode"] == "operational_scenario"
    assert body["horizon_hours"] == 24
    assert body["target_asset"]["asset_id"] == pump["id"]
    assert body["target_asset"]["zone"] == pump["zone"]
    assert body["scenario"]["key"] == ScenarioKey.PUMP_FAILURE.value
    assert "baseline" in body and "metrics" in body["baseline"]
    assert "predicted" in body and "metric_deltas" in body["predicted"]
    assert isinstance(body["affected_assets"], list)
    assert pump["zone"] in body["affected_zones"]
    assert body["operational_risk"]
    assert body["service_impact"]
    assert isinstance(body["mitigations"], list)
    # Phase 16.0 — explicit dependency graph replaces unavailable stub.
    assert body["dependencies"]["available"] is True
    assert isinstance(body["dependencies"].get("depends_on"), list)
    assert isinstance(body["dependencies"].get("depended_by"), list)
    assert body["dependencies"]["message"]


def test_horizon_affects_intensity(client: TestClient) -> None:
    pump = _pick_pump(client)
    short = client.post(
        "/api/simulations/what-if",
        json={
            "asset_id": pump["id"],
            "scenario_key": ScenarioKey.PUMP_FAILURE.value,
            "horizon_hours": 6,
        },
    ).json()
    long = client.post(
        "/api/simulations/what-if",
        json={
            "asset_id": pump["id"],
            "scenario_key": ScenarioKey.PUMP_FAILURE.value,
            "horizon_hours": 48,
        },
    ).json()
    assert short["intensity"] < long["intensity"]


def test_simulation_does_not_mutate_live_telemetry_or_scenario(client: TestClient) -> None:
    """What-if must not change persisted scenario selection (live sim may still tick)."""
    from unittest.mock import patch

    pump = _pick_pump(client)
    asset_id = pump["id"]

    before_scenario = client.get("/api/scenarios/current").json()

    with (
        patch("app.scenarios.engine.ScenarioEngine.select_scenario") as select_mock,
        patch("app.scenarios.engine.ScenarioEngine.set_zone_scenarios") as zone_mock,
        patch("app.domain.repositories.telemetry_repository.TelemetryRepository.add") as add_mock,
    ):
        run = client.post(
            "/api/simulations/what-if",
            json={
                "asset_id": asset_id,
                "scenario_key": ScenarioKey.POWER_OUTAGE.value,
                "horizon_hours": 24,
            },
        )
        assert run.status_code == 200
        select_mock.assert_not_called()
        zone_mock.assert_not_called()
        add_mock.assert_not_called()

    after_scenario = client.get("/api/scenarios/current").json()
    assert after_scenario["key"] == before_scenario["key"]
    assert after_scenario["intensity"] == before_scenario["intensity"]
    assert after_scenario.get("zone_scenarios") == before_scenario.get("zone_scenarios")

    body = run.json()
    assert body["physics_based"] is False
    # Projection should differ from baseline for at least one stressed metric when available.
    changing = [
        d
        for d in body["predicted"]["metric_deltas"]
        if d.get("direction") in {"increase", "decrease"}
    ]
    if body["baseline"]["metrics"]:
        assert changing, "Expected rule-based deltas when baseline metrics exist"


def test_baseline_endpoint_matches_asset_id(client: TestClient) -> None:
    pump = _pick_pump(client)
    response = client.get(f"/api/simulations/baseline/{pump['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["asset_id"] == pump["id"]
    assert body["zone"] == pump["zone"]
    assert isinstance(body["metrics"], list)
