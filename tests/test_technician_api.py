"""Technician API tests (Phase 7D)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db.seed import seed_all
from app.db.session import SessionLocal
from app.domain.enums import TechnicianAvailability
from app.domain.models import Technician
from app.domain.repositories import TechnicianRepository
from app.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _create_technician(
    *,
    zone_id: str = "zone-a",
    availability: str = TechnicianAvailability.AVAILABLE.value,
    tech_id: str | None = None,
) -> str:
    resolved_id = tech_id or f"TEC-TEST-{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        TechnicianRepository(db).save(
            Technician(
                id=resolved_id,
                name=f"Test Technician {resolved_id[-4:]}",
                role="Field Engineer",
                primary_zone_id=zone_id,
                skills=["hydraulics", "pumps"],
                utility_kinds=["water"],
                availability=availability,
                emergency_certified=False,
            )
        )
        return resolved_id
    finally:
        db.close()


def _delete_technician(tech_id: str) -> None:
    db = SessionLocal()
    try:
        tech = TechnicianRepository(db).get(tech_id)
        if tech:
            TechnicianRepository(db).delete(tech)
    finally:
        db.close()


def test_openapi_includes_technician_routes() -> None:
    paths = app.openapi()["paths"]
    assert "/api/technicians" in paths
    assert "/api/technicians/{technician_id}" in paths


def test_get_technician_list(client: TestClient) -> None:
    tech_id = _create_technician()
    try:
        response = client.get("/api/technicians")
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body, list)
        ids = {row["id"] for row in body}
        assert tech_id in ids
        row = next(r for r in body if r["id"] == tech_id)
        assert row["name"].startswith("Test Technician")
        assert row["role"] == "Field Engineer"
        assert row["primary_zone_id"] == "zone-a"
        assert row["skills"] == ["hydraulics", "pumps"]
        assert row["availability"] == TechnicianAvailability.AVAILABLE.value
        assert row["emergency_certified"] is False
        assert "created_at" in row
        assert "updated_at" in row
    finally:
        _delete_technician(tech_id)


def test_get_technician_by_id(client: TestClient) -> None:
    tech_id = _create_technician(zone_id="zone-c", availability=TechnicianAvailability.ON_TASK.value)
    try:
        response = client.get(f"/api/technicians/{tech_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == tech_id
        assert body["primary_zone_id"] == "zone-c"
        assert body["availability"] == TechnicianAvailability.ON_TASK.value
    finally:
        _delete_technician(tech_id)


def test_get_technician_not_found(client: TestClient) -> None:
    response = client.get("/api/technicians/TEC-DOES-NOT-EXIST")
    assert response.status_code == 404


def test_list_filter_by_primary_zone_id(client: TestClient) -> None:
    tech_a = _create_technician(zone_id="zone-a")
    tech_b = _create_technician(zone_id="zone-b")
    try:
        response = client.get("/api/technicians", params={"primary_zone_id": "zone-a"})
        assert response.status_code == 200
        ids = {row["id"] for row in response.json()}
        assert tech_a in ids
        assert tech_b not in ids
    finally:
        _delete_technician(tech_a)
        _delete_technician(tech_b)


def test_list_filter_by_availability(client: TestClient) -> None:
    available_id = _create_technician(availability=TechnicianAvailability.AVAILABLE.value)
    off_shift_id = _create_technician(availability=TechnicianAvailability.OFF_SHIFT.value)
    try:
        response = client.get(
            "/api/technicians",
            params={"availability": TechnicianAvailability.OFF_SHIFT.value},
        )
        assert response.status_code == 200
        ids = {row["id"] for row in response.json()}
        assert off_shift_id in ids
        assert available_id not in ids
    finally:
        _delete_technician(available_id)
        _delete_technician(off_shift_id)


def test_list_filter_returns_empty_when_no_match(client: TestClient) -> None:
    tech_id = _create_technician(zone_id="zone-d")
    try:
        response = client.get(
            "/api/technicians",
            params={"primary_zone_id": "zone-does-not-exist"},
        )
        assert response.status_code == 200
        assert response.json() == []
    finally:
        _delete_technician(tech_id)


def test_seed_persists_technicians(client: TestClient) -> None:
    seed_all()
    response = client.get("/api/technicians")
    assert response.status_code == 200
    body = response.json()
    ids = {row["id"] for row in body}
    for expected in (
        "TEC-A-01",
        "TEC-A-02",
        "TEC-B-01",
        "TEC-B-02",
        "TEC-C-01",
        "TEC-C-02",
        "TEC-D-01",
        "TEC-D-02",
    ):
        assert expected in ids

    tec_a1 = next(row for row in body if row["id"] == "TEC-A-01")
    assert tec_a1["name"] == "Arjun Mehta"
    assert tec_a1["role"] == "Hydraulics Specialist"
    assert tec_a1["primary_zone_id"] == "zone-a"
    assert tec_a1["emergency_certified"] is True
    assert "hydraulics" in tec_a1["skills"]
