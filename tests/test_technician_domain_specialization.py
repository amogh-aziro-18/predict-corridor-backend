"""Phase 14.9 — technician utility domain specialization."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db.seed import seed_all
from app.db.seed.technicians_catalog import (
    GAS_TECHNICIAN_IDS,
    POWER_TECHNICIAN_IDS,
    WATER_TECHNICIAN_IDS,
)
from app.db.session import SessionLocal
from app.domain.enums import TechnicianAvailability
from app.domain.models import Technician
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


def test_seed_preserves_water_and_adds_power(client: TestClient) -> None:
    seed_all()
    seed_all()  # idempotent
    body = client.get("/api/technicians").json()
    ids = {row["id"] for row in body}
    for tid in WATER_TECHNICIAN_IDS:
        assert tid in ids
    for tid in POWER_TECHNICIAN_IDS:
        assert tid in ids
    for tid in GAS_TECHNICIAN_IDS:
        assert tid in ids
    assert len(WATER_TECHNICIAN_IDS) == 8
    assert len(POWER_TECHNICIAN_IDS) == 4
    assert len(GAS_TECHNICIAN_IDS) == 4

    water_rows = [r for r in body if r["id"] in WATER_TECHNICIAN_IDS]
    assert all("water" in r["utility_kinds"] for r in water_rows)
    power_rows = [r for r in body if r["id"] in POWER_TECHNICIAN_IDS]
    assert all(r["utility_kinds"] == ["power"] for r in power_rows)

    tec = next(r for r in body if r["id"] == "TEC-A-01")
    assert tec["name"] == "Arjun Mehta"
    assert tec["utility_kinds"] == ["water"]


def test_filter_utility_water_and_power(client: TestClient) -> None:
    seed_all()
    water = client.get("/api/technicians", params={"utility": "water"}).json()
    power = client.get("/api/technicians", params={"utility": "power"}).json()
    all_rows = client.get("/api/technicians").json()

    water_ids = {r["id"] for r in water}
    power_ids = {r["id"] for r in power}
    assert set(WATER_TECHNICIAN_IDS).issubset(water_ids)
    assert set(POWER_TECHNICIAN_IDS).issubset(power_ids)
    # Water-only / Power-only specialists do not cross into each other's filter.
    assert not set(POWER_TECHNICIAN_IDS) & water_ids
    assert not set(WATER_TECHNICIAN_IDS) & power_ids
    assert len(all_rows) >= len(WATER_TECHNICIAN_IDS) + len(POWER_TECHNICIAN_IDS)


def test_seed_preserves_availability(client: TestClient) -> None:
    seed_all()
    db = SessionLocal()
    try:
        tech = TechnicianRepository(db).get("TEC-A-01")
        assert tech is not None
        tech.availability = TechnicianAvailability.OFF_SHIFT.value
        TechnicianRepository(db).save(tech)
    finally:
        db.close()

    seed_all()
    row = client.get("/api/technicians/TEC-A-01").json()
    assert row["availability"] == TechnicianAvailability.OFF_SHIFT.value
    assert row["utility_kinds"] == ["water"]


def test_water_wo_rejects_power_technician(client: TestClient) -> None:
    seed_all()
    assets = client.get("/api/assets", params={"utility": "water"}).json()
    assert assets
    asset_id = assets[0]["id"]
    created = client.post(
        "/api/work-orders",
        json={
            "asset_id": asset_id,
            "title": "Water domain assign test",
            "priority": "medium",
            "source": "manual",
        },
    )
    assert created.status_code in {200, 201}
    wo_id = created.json()["id"]
    try:
        bad = client.patch(
            f"/api/work-orders/{wo_id}/status",
            json={
                "status": "assigned",
                "assigned_technician_id": "TEC-P-01",
                "actor": "phase-14.9-test",
            },
        )
        assert bad.status_code in {400, 422}
        assert "not qualified" in bad.json().get("detail", "").lower() or "qualified" in str(
            bad.json()
        ).lower()

        good = client.patch(
            f"/api/work-orders/{wo_id}/status",
            json={
                "status": "assigned",
                "assigned_technician_id": "TEC-A-01",
                "actor": "phase-14.9-test",
            },
        )
        assert good.status_code == 200
        assert good.json()["assigned_technician_id"] == "TEC-A-01"
    finally:
        _delete_wo(wo_id)


def test_power_wo_accepts_power_technician(client: TestClient) -> None:
    seed_all()
    assets = client.get("/api/assets", params={"utility": "power"}).json()
    assert assets
    asset_id = next(
        (a["id"] for a in assets if str(a["id"]).startswith("XFMR-")),
        assets[0]["id"],
    )
    created = client.post(
        "/api/work-orders",
        json={
            "asset_id": asset_id,
            "title": "Power domain assign test",
            "priority": "high",
            "source": "manual",
        },
    )
    assert created.status_code in {200, 201}
    wo_id = created.json()["id"]
    try:
        bad = client.patch(
            f"/api/work-orders/{wo_id}/status",
            json={
                "status": "assigned",
                "assigned_technician_id": "TEC-A-01",
                "actor": "phase-14.9-test",
            },
        )
        assert bad.status_code in {400, 422}

        good = client.patch(
            f"/api/work-orders/{wo_id}/status",
            json={
                "status": "assigned",
                "assigned_technician_id": "TEC-P-02",
                "actor": "phase-14.9-test",
            },
        )
        assert good.status_code == 200
        assert good.json()["assigned_technician_id"] == "TEC-P-02"
        assert good.json()["utility"] == "power"
    finally:
        _delete_wo(wo_id)
