"""Phase 14.11 — corridor operational posture composition."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services.dashboard_service import (
    compose_corridor_posture,
    power_condition_to_posture,
    water_signals_to_posture,
)


def test_water_signals_to_posture_bands() -> None:
    assert (
        water_signals_to_posture(
            overall_health=95.0, critical_assets=0, active_alerts=0, api_status="healthy"
        )
        == "HEALTHY"
    )
    assert (
        water_signals_to_posture(
            overall_health=88.0, critical_assets=0, active_alerts=1, api_status="healthy"
        )
        == "STABLE"
    )
    assert (
        water_signals_to_posture(
            overall_health=70.0, critical_assets=1, active_alerts=0, api_status="healthy"
        )
        == "DEGRADED"
    )
    assert (
        water_signals_to_posture(
            overall_health=40.0, critical_assets=0, active_alerts=0, api_status="healthy"
        )
        == "CRITICAL"
    )


def test_power_condition_to_posture_mapping() -> None:
    assert power_condition_to_posture("healthy", api_status="healthy") == "HEALTHY"
    assert power_condition_to_posture("warning", api_status="healthy") == "STABLE"
    assert power_condition_to_posture("high_risk", api_status="healthy") == "DEGRADED"
    assert power_condition_to_posture("critical", api_status="healthy") == "CRITICAL"
    assert power_condition_to_posture("unavailable", api_status="healthy") == "UNAVAILABLE"


def test_compose_corridor_worst_of() -> None:
    assert compose_corridor_posture(["HEALTHY", "STABLE"]) == "STABLE"
    assert compose_corridor_posture(["HEALTHY", "CRITICAL"]) == "CRITICAL"
    assert compose_corridor_posture(["DEGRADED", "UNAVAILABLE"]) == "DEGRADED"
    assert compose_corridor_posture(["UNAVAILABLE", "UNAVAILABLE"]) == "UNAVAILABLE"


def test_corridor_dashboard_endpoint() -> None:
    client = TestClient(app)
    res = client.get("/api/dashboard/corridor")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["overall_posture"] in {
        "HEALTHY",
        "STABLE",
        "DEGRADED",
        "CRITICAL",
        "UNAVAILABLE",
    }
    assert body["assessment_kind"] == "composed_utility_operational_posture"
    assert "fabricated" not in body["explanation"].lower() or "not a fabricated" in body[
        "explanation"
    ].lower()
    kinds = {u["utility"] for u in body["utilities"]}
    assert kinds == {"water", "power", "gas", "communications"}
    water = next(u for u in body["utilities"] if u["utility"] == "water")
    power = next(u for u in body["utilities"] if u["utility"] == "power")
    gas = next(u for u in body["utilities"] if u["utility"] == "gas")
    communications = next(
        u for u in body["utilities"] if u["utility"] == "communications"
    )
    assert water["overall_health"] is not None
    assert power["overall_condition"] is not None
    assert gas["overall_condition"] is not None
    assert communications["overall_condition"] is not None
    assert isinstance(body["total_assets"], int)
    assert isinstance(body["total_active_alerts"], int)


def test_water_and_power_dashboards_still_work() -> None:
    client = TestClient(app)
    water = client.get("/api/dashboard/water")
    power = client.get("/api/dashboard/power")
    assert water.status_code == 200
    assert power.status_code == 200
    assert water.json()["utility"] == "water"
    assert power.json()["utility"] == "power"
    assert "overall_health" in water.json()
    assert "overall_condition" in power.json()
