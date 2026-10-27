"""Phase 17.8a — Communications technicians, WO path, Power→Comm dependency, corridor."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db.seed import seed_all
from app.db.seed.dependencies_catalog import DEPENDENCY_EDGES, EXPECTED_DEPENDENCY_COUNT
from app.db.seed.technicians_catalog import (
    COMMUNICATIONS_TECHNICIAN_IDS,
    GAS_TECHNICIAN_IDS,
    POWER_TECHNICIAN_IDS,
    WATER_TECHNICIAN_IDS,
)
from app.db.session import SessionLocal
from app.domain.enums import DependencyType
from app.domain.models import Asset, AssetDependency, WorkOrder
from app.domain.repositories import TechnicianRepository, WorkOrderRepository
from app.main import app
from app.services.cross_utility_impact_service import CrossUtilityImpactService
from app.services.dashboard_service import DashboardService
from app.services.recommendation_service import RecommendationService
from app.utilities.communications.seed_catalog import COMMUNICATIONS_ASSET_IDS


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _delete_wo(wo_id: str) -> None:
    db = SessionLocal()
    try:
        repo = WorkOrderRepository(db)
        row = repo.get(wo_id)
        if row:
            repo.delete(row)
    finally:
        db.close()


def _pick_comm_asset(client: TestClient) -> dict:
    assets = client.get("/api/assets", params={"utility": "communications"}).json()
    assert assets, "Communications assets must be seeded"
    pop = next((a for a in assets if str(a.get("id", "")).startswith("POP-")), None)
    return pop or assets[0]


# --- Technicians -----------------------------------------------------------


def test_communications_technicians_seeded(client: TestClient) -> None:
    seed_all()
    seed_all()
    body = client.get("/api/technicians").json()
    ids = {row["id"] for row in body}
    assert len(COMMUNICATIONS_TECHNICIAN_IDS) == 4
    for tid in COMMUNICATIONS_TECHNICIAN_IDS:
        assert tid in ids
    rows = [r for r in body if r["id"] in COMMUNICATIONS_TECHNICIAN_IDS]
    assert all(r["utility_kinds"] == ["communications"] for r in rows)
    assert {r["primary_zone_id"] for r in rows} == {
        "zone-a",
        "zone-b",
        "zone-c",
        "zone-d",
    }


def test_technician_filter_and_qualification_communications(client: TestClient) -> None:
    seed_all()
    comm = client.get("/api/technicians", params={"utility": "communications"}).json()
    comm_ids = {r["id"] for r in comm}
    assert set(COMMUNICATIONS_TECHNICIAN_IDS).issubset(comm_ids)
    # Water/Power/Gas-only specialists must not appear.
    assert not set(WATER_TECHNICIAN_IDS) & comm_ids
    assert not set(POWER_TECHNICIAN_IDS) & comm_ids
    assert not set(GAS_TECHNICIAN_IDS) & comm_ids

    db = SessionLocal()
    try:
        from app.domain.repositories.technician_repository import (
            technician_qualified_for_utility,
        )

        tech = TechnicianRepository(db).get("TEC-C-COM-01")
        assert tech is not None
        assert technician_qualified_for_utility(tech, "communications")
        assert not technician_qualified_for_utility(tech, "water")
        water = TechnicianRepository(db).get("TEC-A-01")
        assert water is not None
        assert not technician_qualified_for_utility(water, "communications")
    finally:
        db.close()


# --- Work orders -----------------------------------------------------------


def test_comm_recommendation_creates_wo_with_snapshot(client: TestClient) -> None:
    seed_all()
    asset = _pick_comm_asset(client)
    rec_id = f"rec-comm-{uuid.uuid4().hex[:8]}"
    snapshot = {
        "id": rec_id,
        "priority": "high",
        "severity": "high",
        "title": "Communications path quality stress",
        "description": "Rule-based assessment for edge PoP path quality.",
        "affected_asset": asset["id"],
        "affected_zone": asset["zone"],
        "recommendation": (
            f"Investigate combined latency and packet loss on {asset['id']}; "
            "compare adjacent communications assets in the same zone."
        ),
        "estimated_business_impact": "Degraded path quality may progress to outage.",
        "confidence": 0.84,
        "generated_at": "2026-10-16T12:00:00+00:00",
        "rule_code": "communications_path_quality_stress",
        "scenario_key": "normal",
    }
    created = client.post(
        "/api/work-orders",
        json={
            "asset_id": asset["id"],
            "title": snapshot["title"],
            "description": snapshot["description"],
            "priority": "high",
            "source": "recommendation",
            "recommendation_id": rec_id,
            "recommendation_snapshot": snapshot,
            "created_by": "phase-17.8a-test",
        },
    )
    assert created.status_code in {200, 201}, created.text
    body = created.json()
    try:
        assert body["utility"] == "communications"
        assert body["asset_id"] == asset["id"]
        assert body["zone_id"] == asset["zone"]
        assert body["recommendation_id"] == rec_id
        assert body["recommendation_snapshot"] == snapshot
        assert body["status"] == "open"
        assert body["source"] == "recommendation"
    finally:
        _delete_wo(body["id"])


def test_comm_wo_assignment_and_reject_unqualified(client: TestClient) -> None:
    seed_all()
    asset = _pick_comm_asset(client)
    created = client.post(
        "/api/work-orders",
        json={
            "asset_id": asset["id"],
            "title": "Comm assign test",
            "priority": "high",
            "source": "manual",
            "created_by": "phase-17.8a-test",
        },
    )
    assert created.status_code in {200, 201}, created.text
    wo_id = created.json()["id"]
    try:
        assert created.json()["utility"] == "communications"
        bad = client.patch(
            f"/api/work-orders/{wo_id}/status",
            json={
                "status": "assigned",
                "assigned_technician_id": "TEC-A-01",
                "actor": "phase-17.8a-test",
            },
        )
        assert bad.status_code in {400, 422}, bad.text
        assert "qualified" in str(bad.json()).lower()

        bad_p = client.patch(
            f"/api/work-orders/{wo_id}/status",
            json={
                "status": "assigned",
                "assigned_technician_id": "TEC-P-01",
                "actor": "phase-17.8a-test",
            },
        )
        assert bad_p.status_code in {400, 422}

        good = client.patch(
            f"/api/work-orders/{wo_id}/status",
            json={
                "status": "assigned",
                "assigned_technician_id": "TEC-C-COM-01",
                "actor": "phase-17.8a-test",
            },
        )
        assert good.status_code == 200, good.text
        assert good.json()["assigned_technician_id"] == "TEC-C-COM-01"
        assert good.json()["utility"] == "communications"
    finally:
        _delete_wo(wo_id)


def test_comm_wo_full_lifecycle(client: TestClient) -> None:
    seed_all()
    asset = _pick_comm_asset(client)
    created = client.post(
        "/api/work-orders",
        json={
            "asset_id": asset["id"],
            "title": "Comm lifecycle",
            "priority": "medium",
            "source": "manual",
            "created_by": "phase-17.8a-test",
        },
    )
    assert created.status_code in {200, 201}, created.text
    wo_id = created.json()["id"]
    try:
        assert created.json()["status"] == "open"
        assigned = client.patch(
            f"/api/work-orders/{wo_id}/status",
            json={
                "status": "assigned",
                "assigned_technician_id": "TEC-C-COM-02",
                "actor": "phase-17.8a-test",
            },
        )
        assert assigned.status_code == 200
        assert assigned.json()["status"] == "assigned"

        for status in ("in_progress", "completed", "closed"):
            res = client.patch(
                f"/api/work-orders/{wo_id}/status",
                json={"status": status, "actor": "phase-17.8a-test"},
            )
            assert res.status_code == 200, res.text
            assert res.json()["status"] == status
            again = client.get(f"/api/work-orders/{wo_id}")
            assert again.json()["status"] == status
            assert again.json()["utility"] == "communications"
    finally:
        _delete_wo(wo_id)


def test_listing_recommendations_creates_zero_work_orders(client: TestClient) -> None:
    seed_all()
    db = SessionLocal()
    try:
        before = db.query(WorkOrder).count()
    finally:
        db.close()
    res = client.get("/api/recommendations")
    assert res.status_code == 200, res.text
    # Service path also used by RecommendationService
    db = SessionLocal()
    try:
        RecommendationService(db).list_recommendations()
        after = db.query(WorkOrder).count()
        assert after == before
    finally:
        db.close()


# --- Dependency ------------------------------------------------------------


def test_power_to_communications_supplies_edge_documented() -> None:
    seed_all()
    edge = next(e for e in DEPENDENCY_EDGES if e["id"] == "DEP-POC-004")
    assert edge["source_asset_id"] == "FDR-A-01"
    assert edge["target_asset_id"] == "POP-A-01"
    assert edge["dependency_type"] == DependencyType.SUPPLIES.value
    assert EXPECTED_DEPENDENCY_COUNT == 4
    # Catalog comment/docstring must mark POC assumption (file-level + edge comment).
    from pathlib import Path

    catalog = Path("app/db/seed/dependencies_catalog.py").read_text(encoding="utf-8")
    assert "POC ASSUMPTION" in catalog
    assert "POP-A-01" in catalog
    # No Comm → Water/Power/Gas SUPPLIES edges.
    for e in DEPENDENCY_EDGES:
        if str(e["source_asset_id"]).startswith(("POP-", "GW-")):
            raise AssertionError("Communications must not be a SUPPLIES source")


def test_dependency_seeded_and_impact_traverses(client: TestClient) -> None:
    seed_all()
    res = client.get("/api/dependencies")
    assert res.status_code == 200
    body = res.json()
    assert len(body) == EXPECTED_DEPENDENCY_COUNT
    edge = next(e for e in body if e["id"] == "DEP-POC-004")
    assert edge["source_utility"] == "power"
    assert edge["target_utility"] == "communications"
    assert edge["dependency_type"] == "supplies"

    feeder = client.get("/api/dependencies/FDR-A-01").json()
    dep_ids = {d["asset_id"] for d in feeder["depended_by"]}
    assert "POP-A-01" in dep_ids

    db = SessionLocal()
    try:
        pop = db.query(Asset).filter(Asset.id == "POP-A-01").one()
        health_before = float(pop.health)
        status_before = pop.status
        profile_before = dict(pop.profile or {})

        impact = CrossUtilityImpactService(db).assess_asset_impact(
            asset_id="FDR-A-01",
            condition="unavailable",
        )
        assert impact["mutates_operational_state"] is False
        assert impact["assessment_kind"] == "explicit_dependency_impact"
        affected_ids = {a["asset_id"] for a in impact.get("affected_assets", [])}
        assert "POP-A-01" in affected_ids
        assert "communications" in impact.get("affected_utilities", [])

        db.refresh(pop)
        assert float(pop.health) == health_before
        assert pop.status == status_before
        assert dict(pop.profile or {}) == profile_before
    finally:
        db.close()


def test_no_automatic_dependent_wo_from_impact(client: TestClient) -> None:
    seed_all()
    db = SessionLocal()
    try:
        before = (
            db.query(WorkOrder)
            .filter(WorkOrder.asset_id.in_(list(COMMUNICATIONS_ASSET_IDS)))
            .count()
        )
        CrossUtilityImpactService(db).assess_asset_impact(
            asset_id="FDR-A-01",
            condition="critical",
        )
        after = (
            db.query(WorkOrder)
            .filter(WorkOrder.asset_id.in_(list(COMMUNICATIONS_ASSET_IDS)))
            .count()
        )
        assert after == before
    finally:
        db.close()


# --- Corridor --------------------------------------------------------------


def test_corridor_includes_four_live_utilities(client: TestClient) -> None:
    seed_all()
    res = client.get("/api/dashboard/corridor")
    assert res.status_code == 200, res.text
    body = res.json()
    kinds = {u["utility"] for u in body["utilities"]}
    assert kinds == {"water", "power", "gas", "communications"}

    comm = next(u for u in body["utilities"] if u["utility"] == "communications")
    assert comm["asset_count"] == len(COMMUNICATIONS_ASSET_IDS)
    assert comm["asset_count"] == 8  # seeded truth — not mock 132
    assert comm["overall_condition"] is not None
    assert comm["api_status"] in {"healthy", "insufficient_data"}

    # Live dashboard endpoint also exposes Comm.
    dash = client.get("/api/dashboard/communications")
    assert dash.status_code == 200, dash.text
    assert dash.json()["utility"] == "communications"
    assert dash.json()["asset_count"] == 8
    assert len(dash.json()["zone_summary"]) == 4
    zone_ids = {z["zone_id"] for z in dash.json()["zone_summary"]}
    assert zone_ids == {"zone-a", "zone-b", "zone-c", "zone-d"}


def test_corridor_comm_posture_from_live_service() -> None:
    seed_all()
    db = SessionLocal()
    try:
        svc = DashboardService(db)
        dash = svc.communications_dashboard()
        corridor = svc.corridor_dashboard()
        slice_ = next(u for u in corridor.utilities if u.utility == "communications")
        assert slice_.asset_count == dash.asset_count
        assert slice_.overall_health == dash.overall_health
        assert slice_.overall_condition == dash.overall_condition
        assert slice_.active_alerts == dash.active_alerts
        # Water/Power/Gas still present with live counts.
        for kind in ("water", "power", "gas"):
            assert any(u.utility == kind for u in corridor.utilities)
    finally:
        db.close()


def test_four_zone_comm_assets_present() -> None:
    seed_all()
    db = SessionLocal()
    try:
        rows = (
            db.query(Asset)
            .filter(Asset.utility_kind == "communications")
            .all()
        )
        assert {a.id for a in rows} == set(COMMUNICATIONS_ASSET_IDS)
        by_zone = {z: [] for z in ("zone-a", "zone-b", "zone-c", "zone-d")}
        for a in rows:
            by_zone[a.zone_id].append(a.id)
        assert sorted(by_zone["zone-a"]) == ["GW-A-01", "POP-A-01"]
        assert sorted(by_zone["zone-b"]) == ["GW-B-01", "POP-B-01"]
        assert sorted(by_zone["zone-c"]) == ["GW-C-01", "POP-C-01"]
        assert sorted(by_zone["zone-d"]) == ["GW-D-01", "POP-D-01"]
    finally:
        db.close()


def test_dependency_edge_row_exists_in_db() -> None:
    seed_all()
    db = SessionLocal()
    try:
        row = db.query(AssetDependency).filter(AssetDependency.id == "DEP-POC-004").one()
        assert row.source_asset_id == "FDR-A-01"
        assert row.target_asset_id == "POP-A-01"
        assert row.dependency_type == "supplies"
        assert row.enabled is True
    finally:
        db.close()
