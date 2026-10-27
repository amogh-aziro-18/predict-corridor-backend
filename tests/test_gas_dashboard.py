"""Phase 15.5 — Gas dashboard aggregation for Command Center."""

from __future__ import annotations

from app.db.seed import seed_all
from app.db.session import SessionLocal
from app.services.dashboard_service import DashboardService
from app.simulator.gas.asset_simulator import GasAssetSimulator
from app.simulator.gas.scenarios import GasScenarioMode
from fastapi.testclient import TestClient

from app.main import app


def test_gas_dashboard_includes_four_zones() -> None:
    seed_all()
    db = SessionLocal()
    try:
        sim = GasAssetSimulator(db, seed=7, mode=GasScenarioMode.MIXED)
        for _ in range(8):
            sim.persist_tick()
        dash = DashboardService(db).gas_dashboard()
        assert dash.utility == "gas"
        assert dash.assessment_kind == "rule_based_condition_assessment"
        assert dash.asset_count == 12
        zone_ids = [z.zone_id for z in dash.zone_summary]
        assert zone_ids[:4] == ["zone-a", "zone-b", "zone-c", "zone-d"]
        assert all(z.asset_count >= 1 for z in dash.zone_summary)
        # Condition comes from telemetry assessment — not hardcoded zone labels.
        assert dash.overall_condition in {
            "healthy",
            "warning",
            "high_risk",
            "critical",
            "unavailable",
        }
    finally:
        db.close()


def test_gas_dashboard_api_route() -> None:
    client = TestClient(app)
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/dashboard/gas" in paths
    resp = client.get("/api/dashboard/gas")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["utility"] == "gas"
    assert "zone_summary" in body
