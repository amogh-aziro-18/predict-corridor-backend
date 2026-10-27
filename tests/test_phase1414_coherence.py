"""Phase 14.14 — Resilience diagnostic, emergency status, parameter what-if."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db.seed import seed_all
from app.main import app
from app.services.parameter_simulation import project_parameter
from app.utilities.power import health as power_health


def test_resilience_diagnostic_endpoint() -> None:
    seed_all()
    client = TestClient(app)
    res = client.get("/api/resilience/diagnostic")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["overall_posture"] in {"HEALTHY", "STABLE", "AT RISK", "CRITICAL", "UNAVAILABLE"}
    assert "why" in body and body["why"]
    ids = {s["id"] for s in body["slices"]}
    assert {
        "water",
        "power",
        "gas",
        "communications",
        "environment",
        "weather",
        "operations",
    } <= ids
    # No fabricated numeric score field
    assert "score" not in body
    assert "not a 0–100" in body["note"].lower() or "not a 0-100" in body["note"].lower()
    comm = next(s for s in body["slices"] if s["id"] == "communications")
    assert comm["kind"] == "operational"
    assert "asset_count" in comm["metrics"]
    gas = next(s for s in body["slices"] if s["id"] == "gas")
    assert gas["kind"] == "operational"


def test_emergency_status_no_invention_from_empty() -> None:
    seed_all()
    client = TestClient(app)
    res = client.get("/api/emergency/status")
    assert res.status_code == 200, res.text
    body = res.json()
    assert "has_active_emergency" in body
    assert body["qualification"]["rules"]
    assert "context" in body
    # Message is explicit either way
    assert "EMERGENCY" in body["message"]
    if not body["has_active_emergency"]:
        assert body["message"].startswith("NO ACTIVE EMERGENCY")
        assert body["events"] == []


def test_parameter_what_if_power_load_explainable() -> None:
    seed_all()
    client = TestClient(app)
    assets = client.get("/api/assets", params={"utility": "power"}).json()
    assert assets, "expected power assets"
    asset_id = assets[0]["id"]

    params = client.get(f"/api/simulations/parameters/{asset_id}")
    assert params.status_code == 200, params.text
    catalog = params.json()["parameters"]
    load = next((p for p in catalog if p["parameter"] == "load_pct"), None)
    assert load is not None

    if not load["available"]:
        # Honest unavailable when no telemetry — still a valid Phase 14.14 outcome
        run = client.post(
            "/api/simulations/parameter-what-if",
            json={"asset_id": asset_id, "parameter": "load_pct", "delta_percent": 10},
        )
        assert run.status_code == 200
        body = run.json()
        assert body["available"] is False
        assert "unavailable" in body["explanation"].lower()
        return

    run = client.post(
        "/api/simulations/parameter-what-if",
        json={"asset_id": asset_id, "parameter": "load_pct", "delta_percent": 10},
    )
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["physics_based"] is False
    assert body["available"] is True
    assert body["baseline_value"] is not None
    assert body["projected_value"] is not None
    assert abs(body["projected_value"] - body["baseline_value"] * 1.1) < 0.05
    assert body["explanation"]
    assert body["rule_refs"]
    assert "LOAD_CRITICAL" in " ".join(body["rule_refs"]) or str(power_health.LOAD_CRITICAL) in body["explanation"]


def test_parameter_projection_unit_rules() -> None:
    proj = project_parameter(
        utility_kind="power",
        asset_type="transformer",
        parameter="load_pct",
        baseline_value=92.0,
        delta_percent=10.0,
        absolute_value=None,
    )
    assert proj.available
    assert abs(proj.projected_value - 101.2) < 0.01
    assert proj.condition == "CRITICAL"
    assert proj.potential_alert is True
    assert "LOAD_CRITICAL" in " ".join(proj.rule_refs) or "98" in proj.explanation


def test_parameter_unavailable_without_baseline() -> None:
    proj = project_parameter(
        utility_kind="water",
        asset_type="pump_station",
        parameter="pressure",
        baseline_value=None,
        delta_percent=10.0,
        absolute_value=None,
    )
    assert proj.available is False
    assert "unavailable" in proj.explanation.lower()


def test_legacy_scenario_what_if_still_works() -> None:
    seed_all()
    client = TestClient(app)
    pumps = client.get(
        "/api/assets", params={"utility": "water", "asset_type": "pump_station"}
    ).json()
    if not pumps:
        return
    res = client.post(
        "/api/simulations/what-if",
        json={
            "asset_id": pumps[0]["id"],
            "scenario_key": "normal_operations",
            "horizon_hours": 24,
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["physics_based"] is False
