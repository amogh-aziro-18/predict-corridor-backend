"""Phase 14.8 — Water + Power WO lifecycle persistence (operator-driven, no auto-create)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db.seed import seed_all
from app.db.session import SessionLocal
from app.domain.enums import TechnicianAvailability
from app.domain.models import Technician
from app.domain.repositories import TechnicianRepository, WorkOrderRepository
from app.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _tech(zone_id: str, utility: str = "water") -> str:
    tech_id = f"TEC-148-{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        TechnicianRepository(db).save(
            Technician(
                id=tech_id,
                name="Phase 14.8 Technician",
                role="Field Engineer",
                primary_zone_id=zone_id,
                skills=["general"],
                utility_kinds=[utility],
                availability=TechnicianAvailability.AVAILABLE.value,
            )
        )
        return tech_id
    finally:
        db.close()


def _delete_tech(tech_id: str) -> None:
    db = SessionLocal()
    try:
        row = TechnicianRepository(db).get(tech_id)
        if row:
            TechnicianRepository(db).delete(row)
    finally:
        db.close()


def _delete_wo(wo_id: str) -> None:
    db = SessionLocal()
    try:
        repo = WorkOrderRepository(db)
        row = repo.get(wo_id)
        if row:
            repo.delete(row)
    finally:
        db.close()


@pytest.mark.parametrize(
    "utility,asset_prefix",
    [
        ("water", "PMP-"),
        ("power", "XFMR-"),
        ("gas", "CMP-"),
    ],
)
def test_water_and_power_wo_lifecycle_persists(
    client: TestClient,
    utility: str,
    asset_prefix: str,
) -> None:
    """
    Operator flow: create → assign → in_progress → completed → closed.

    Covers Water, Power, and Gas. Re-GET after each step proves PostgreSQL
    persistence (not session-only). Alerts/recommendations do not auto-create
    these work orders.
    """
    seed_all()
    assets = client.get("/api/assets", params={"utility": utility}).json()
    asset = next((a for a in assets if str(a.get("id", "")).startswith(asset_prefix)), None)
    if asset is None and assets:
        asset = assets[0]
    if asset is None:
        pytest.skip(f"No {utility} assets seeded")

    asset_id = asset["id"]
    zone_id = asset.get("zone") or asset.get("zone_id") or "zone-a"
    tech_id = _tech(str(zone_id), utility=utility)
    wo_id: str | None = None

    try:
        created = client.post(
            "/api/work-orders",
            json={
                "asset_id": asset_id,
                "title": f"Phase 14.8 {utility} lifecycle",
                "description": "Operator-created from real asset",
                "priority": "high",
                "source": "manual",
                "created_by": "phase-14.8-test",
            },
        )
        assert created.status_code in {200, 201}, created.text
        body = created.json()
        wo_id = body["id"]
        assert body["status"] == "open"
        assert body["utility"] == utility
        assert body["asset_id"] == asset_id

        # Persistence check after create
        again = client.get(f"/api/work-orders/{wo_id}")
        assert again.status_code == 200
        assert again.json()["status"] == "open"
        assert again.json()["assigned_technician_id"] is None

        assigned = client.patch(
            f"/api/work-orders/{wo_id}/status",
            json={
                "status": "assigned",
                "assigned_technician_id": tech_id,
                "actor": "phase-14.8-test",
            },
        )
        assert assigned.status_code == 200, assigned.text
        assert assigned.json()["status"] == "assigned"
        assert assigned.json()["assigned_technician_id"] == tech_id

        reread = client.get(f"/api/work-orders/{wo_id}")
        assert reread.json()["assigned_technician_id"] == tech_id
        assert reread.json()["status"] == "assigned"

        started = client.patch(
            f"/api/work-orders/{wo_id}/status",
            json={"status": "in_progress", "actor": "phase-14.8-test"},
        )
        assert started.status_code == 200
        assert started.json()["status"] == "in_progress"
        assert started.json()["started_at"] is not None

        assert client.get(f"/api/work-orders/{wo_id}").json()["status"] == "in_progress"

        completed = client.patch(
            f"/api/work-orders/{wo_id}/status",
            json={
                "status": "completed",
                "completion_notes": "Field work done",
                "actor": "phase-14.8-test",
            },
        )
        assert completed.status_code == 200
        assert completed.json()["status"] == "completed"

        # Invalid transition must fail
        bad = client.patch(
            f"/api/work-orders/{wo_id}/status",
            json={"status": "in_progress", "actor": "phase-14.8-test"},
        )
        assert bad.status_code in {400, 409, 422}

        closed = client.patch(
            f"/api/work-orders/{wo_id}/status",
            json={
                "status": "closed",
                "verification_notes": "Verified",
                "actor": "phase-14.8-test",
            },
        )
        assert closed.status_code == 200
        final = client.get(f"/api/work-orders/{wo_id}")
        assert final.json()["status"] == "closed"
        assert final.json()["assigned_technician_id"] == tech_id
        assert len(final.json().get("events") or []) >= 4
    finally:
        if wo_id:
            _delete_wo(wo_id)
        _delete_tech(tech_id)


def test_recommendation_wo_does_not_auto_create_from_alerts(client: TestClient) -> None:
    """Alerts remain independent — listing alerts must not create work orders."""
    seed_all()
    before = client.get("/api/operations/work-orders").json()
    before_ids = {row["id"] for row in before}
    alerts = client.get("/api/alerts", params={"status": "open"}).json()
    # Merely reading alerts must not invent WOs.
    after = client.get("/api/operations/work-orders").json()
    after_ids = {row["id"] for row in after}
    assert after_ids == before_ids
    assert isinstance(alerts, list)
