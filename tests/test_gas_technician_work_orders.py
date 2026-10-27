"""Phase 15.6 — Gas technician domain + WO assignment / lifecycle."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db.seed import seed_all
from app.db.seed.technicians_catalog import (
    GAS_TECHNICIAN_IDS,
    MULTI_UTILITY_TECHNICIAN_IDS,
    POWER_TECHNICIAN_IDS,
    WATER_TECHNICIAN_IDS,
)
from app.db.session import SessionLocal
from app.domain.enums import TechnicianAvailability
from app.domain.repositories import TechnicianRepository, WorkOrderRepository
from app.main import app


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


def _create_gas_wo(client: TestClient, *, title: str = "Gas domain WO") -> tuple[str, str]:
    assets = client.get("/api/assets", params={"utility": "gas"}).json()
    assert assets, "Gas assets must be seeded"
    asset = next(
        (a for a in assets if str(a.get("id", "")).startswith("CMP-")),
        assets[0],
    )
    created = client.post(
        "/api/work-orders",
        json={
            "asset_id": asset["id"],
            "title": title,
            "priority": "high",
            "source": "manual",
            "created_by": "phase-15.6-test",
        },
    )
    assert created.status_code in {200, 201}, created.text
    body = created.json()
    assert body["utility"] == "gas"
    assert body["status"] == "open"
    return body["id"], asset["id"]


def test_seed_adds_gas_technicians_idempotently(client: TestClient) -> None:
    seed_all()
    seed_all()
    body = client.get("/api/technicians").json()
    ids = {row["id"] for row in body}

    for tid in WATER_TECHNICIAN_IDS:
        assert tid in ids
    for tid in POWER_TECHNICIAN_IDS:
        assert tid in ids
    for tid in GAS_TECHNICIAN_IDS:
        assert tid in ids
    for tid in MULTI_UTILITY_TECHNICIAN_IDS:
        assert tid in ids

    assert len(GAS_TECHNICIAN_IDS) == 4
    assert len(WATER_TECHNICIAN_IDS) == 8
    assert len(POWER_TECHNICIAN_IDS) == 4

    gas_rows = [r for r in body if r["id"] in GAS_TECHNICIAN_IDS]
    assert all(r["utility_kinds"] == ["gas"] for r in gas_rows)
    zones = {r["primary_zone_id"] for r in gas_rows}
    assert zones == {"zone-a", "zone-b", "zone-c", "zone-d"}

    multi = next(r for r in body if r["id"] == "TEC-M-01")
    assert set(multi["utility_kinds"]) == {"water", "power", "gas"}


def test_filter_utility_gas(client: TestClient) -> None:
    seed_all()
    gas = client.get("/api/technicians", params={"utility": "gas"}).json()
    water = client.get("/api/technicians", params={"utility": "water"}).json()
    power = client.get("/api/technicians", params={"utility": "power"}).json()

    gas_ids = {r["id"] for r in gas}
    water_ids = {r["id"] for r in water}
    power_ids = {r["id"] for r in power}

    assert set(GAS_TECHNICIAN_IDS).issubset(gas_ids)
    assert "TEC-M-01" in gas_ids  # multi-domain qualifies for gas
    # Water-only / Power-only specialists must not appear in gas filter.
    assert not set(WATER_TECHNICIAN_IDS) & gas_ids
    assert not set(POWER_TECHNICIAN_IDS) & gas_ids
    # Gas-only specialists must not appear in water/power-only filters.
    assert not set(GAS_TECHNICIAN_IDS) & water_ids
    assert not set(GAS_TECHNICIAN_IDS) & power_ids
    # Multi appears in each domain filter.
    assert "TEC-M-01" in water_ids
    assert "TEC-M-01" in power_ids


def test_seed_preserves_gas_availability(client: TestClient) -> None:
    seed_all()
    db = SessionLocal()
    try:
        tech = TechnicianRepository(db).get("TEC-G-01")
        assert tech is not None
        tech.availability = TechnicianAvailability.OFF_SHIFT.value
        TechnicianRepository(db).save(tech)
    finally:
        db.close()

    seed_all()
    row = client.get("/api/technicians/TEC-G-01").json()
    assert row["availability"] == TechnicianAvailability.OFF_SHIFT.value
    assert row["utility_kinds"] == ["gas"]


def test_gas_wo_rejects_water_and_power_technicians(client: TestClient) -> None:
    seed_all()
    wo_id, _ = _create_gas_wo(client, title="Gas reject cross-domain")
    try:
        for bad_tech in ("TEC-A-01", "TEC-P-01"):
            bad = client.patch(
                f"/api/work-orders/{wo_id}/status",
                json={
                    "status": "assigned",
                    "assigned_technician_id": bad_tech,
                    "actor": "phase-15.6-test",
                },
            )
            assert bad.status_code in {400, 422}, bad.text
            detail = str(bad.json()).lower()
            assert "qualified" in detail
    finally:
        _delete_wo(wo_id)


def test_gas_wo_accepts_gas_and_multi_technicians(client: TestClient) -> None:
    seed_all()
    wo_id, _ = _create_gas_wo(client, title="Gas accept gas tech")
    try:
        good = client.patch(
            f"/api/work-orders/{wo_id}/status",
            json={
                "status": "assigned",
                "assigned_technician_id": "TEC-G-01",
                "actor": "phase-15.6-test",
            },
        )
        assert good.status_code == 200, good.text
        assert good.json()["assigned_technician_id"] == "TEC-G-01"
        assert good.json()["utility"] == "gas"
    finally:
        _delete_wo(wo_id)

    wo_id2, _ = _create_gas_wo(client, title="Gas accept multi tech")
    try:
        good2 = client.patch(
            f"/api/work-orders/{wo_id2}/status",
            json={
                "status": "assigned",
                "assigned_technician_id": "TEC-M-01",
                "actor": "phase-15.6-test",
            },
        )
        assert good2.status_code == 200, good2.text
        assert good2.json()["assigned_technician_id"] == "TEC-M-01"
    finally:
        _delete_wo(wo_id2)


def test_gas_wo_full_lifecycle_persists(client: TestClient) -> None:
    """
    Operator flow on Gas asset:
    open → assigned → in_progress → completed → closed
    with re-GET after each transition.
    """
    seed_all()
    wo_id, asset_id = _create_gas_wo(client, title="Phase 15.6 Gas lifecycle")
    try:
        again = client.get(f"/api/work-orders/{wo_id}")
        assert again.status_code == 200
        assert again.json()["status"] == "open"
        assert again.json()["utility"] == "gas"
        assert again.json()["asset_id"] == asset_id
        assert again.json()["assigned_technician_id"] is None

        assigned = client.patch(
            f"/api/work-orders/{wo_id}/status",
            json={
                "status": "assigned",
                "assigned_technician_id": "TEC-G-02",
                "actor": "phase-15.6-test",
            },
        )
        assert assigned.status_code == 200, assigned.text
        got = client.get(f"/api/work-orders/{wo_id}").json()
        assert got["status"] == "assigned"
        assert got["assigned_technician_id"] == "TEC-G-02"

        in_progress = client.patch(
            f"/api/work-orders/{wo_id}/status",
            json={"status": "in_progress", "actor": "phase-15.6-test"},
        )
        assert in_progress.status_code == 200, in_progress.text
        assert client.get(f"/api/work-orders/{wo_id}").json()["status"] == "in_progress"

        completed = client.patch(
            f"/api/work-orders/{wo_id}/status",
            json={
                "status": "completed",
                "actor": "phase-15.6-test",
                "completion_notes": "Gas inspection complete (POC)",
            },
        )
        assert completed.status_code == 200, completed.text
        assert client.get(f"/api/work-orders/{wo_id}").json()["status"] == "completed"

        closed = client.patch(
            f"/api/work-orders/{wo_id}/status",
            json={
                "status": "closed",
                "actor": "phase-15.6-test",
                "verification_notes": "Operator closed",
            },
        )
        assert closed.status_code == 200, closed.text
        final = client.get(f"/api/work-orders/{wo_id}").json()
        assert final["status"] == "closed"
        assert final["utility"] == "gas"
        assert final["assigned_technician_id"] == "TEC-G-02"
    finally:
        _delete_wo(wo_id)


def test_gas_recommendation_can_create_wo(client: TestClient) -> None:
    """Gas recommendation → operator POST WO (no auto-create)."""
    seed_all()
    # Ensure Gas telemetry stress so OI may produce gas_* drafts; WO still operator-created.
    assets = client.get("/api/assets", params={"utility": "gas"}).json()
    assert assets
    asset_id = next(
        (a["id"] for a in assets if str(a["id"]).startswith("CMP-C")),
        assets[0]["id"],
    )
    created = client.post(
        "/api/work-orders",
        json={
            "asset_id": asset_id,
            "title": "From Gas recommendation (operator)",
            "description": "Operator accepted Gas recommendation manually",
            "priority": "high",
            "source": "recommendation",
            "created_by": "phase-15.6-test",
        },
    )
    assert created.status_code in {200, 201}, created.text
    wo_id = created.json()["id"]
    try:
        body = created.json()
        assert body["utility"] == "gas"
        assert body["source"] == "recommendation"
        assert body["status"] == "open"
        # No automatic assignment.
        assert body["assigned_technician_id"] is None
    finally:
        _delete_wo(wo_id)
