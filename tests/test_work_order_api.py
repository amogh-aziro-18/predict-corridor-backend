"""Work order API and lifecycle tests (Phase 7B)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.domain.enums import TechnicianAvailability, WorkOrderStatus
from app.domain.models import Technician
from app.domain.repositories import (
    AssetRepository,
    TechnicianRepository,
    WorkOrderRepository,
)
from app.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _pick_asset(client: TestClient) -> dict:
    assets = client.get("/api/assets", params={"utility": "water"}).json()
    if not assets:
        pytest.skip("No water assets seeded")
    return assets[0]


def _create_technician(zone_id: str) -> str:
    tech_id = f"TEC-API-{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        TechnicianRepository(db).save(
            Technician(
                id=tech_id,
                name="API Test Technician",
                role="Field Engineer",
                primary_zone_id=zone_id,
                skills=["general"],
                utility_kinds=["water"],
                availability=TechnicianAvailability.AVAILABLE.value,
            )
        )
        return tech_id
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


def _delete_work_order(wo_id: str) -> None:
    db = SessionLocal()
    try:
        wo = WorkOrderRepository(db).get(wo_id)
        if wo:
            WorkOrderRepository(db).delete(wo)
    finally:
        db.close()


def _post_work_order(client: TestClient, asset: dict, **extra: object) -> dict:
    body = {
        "asset_id": asset["id"],
        "title": f"API test work order {uuid.uuid4().hex[:6]}",
        "priority": "high",
        "source": "manual",
        **extra,
    }
    response = client.post("/api/work-orders", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_openapi_includes_work_order_routes() -> None:
    paths = app.openapi()["paths"]
    assert "/api/work-orders" in paths
    assert "/api/work-orders/{work_order_id}" in paths
    assert "/api/work-orders/{work_order_id}/status" in paths
    assert "/api/operations/work-orders" in paths


def test_post_creates_work_order(client: TestClient) -> None:
    asset = _pick_asset(client)
    created = _post_work_order(client, asset)
    try:
        assert created["status"] == "open"
        assert created["asset_id"] == asset["id"]
        assert created["title"].startswith("API test work order")
    finally:
        _delete_work_order(created["id"])


def test_created_work_order_uses_asset_utility_and_zone(client: TestClient) -> None:
    asset = _pick_asset(client)
    created = _post_work_order(client, asset)
    try:
        assert created["utility"] == asset["utility"]
        assert created["zone_id"] == asset["zone"]
    finally:
        _delete_work_order(created["id"])


def test_invalid_asset_returns_404(client: TestClient) -> None:
    response = client.post(
        "/api/work-orders",
        json={
            "asset_id": "DOES-NOT-EXIST-XYZ",
            "title": "Invalid asset test",
        },
    )
    assert response.status_code == 404


def test_get_work_order_by_id(client: TestClient) -> None:
    asset = _pick_asset(client)
    created = _post_work_order(client, asset)
    try:
        response = client.get(f"/api/work-orders/{created['id']}")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == created["id"]
        assert body["asset_id"] == asset["id"]
    finally:
        _delete_work_order(created["id"])


def test_list_work_orders_includes_created_record(client: TestClient) -> None:
    asset = _pick_asset(client)
    created = _post_work_order(client, asset)
    try:
        listing = client.get("/api/work-orders", params={"asset_id": asset["id"]}).json()
        ids = {row["id"] for row in listing}
        assert created["id"] in ids
    finally:
        _delete_work_order(created["id"])


def test_operations_compat_endpoint_returns_real_data(client: TestClient) -> None:
    asset = _pick_asset(client)
    created = _post_work_order(client, asset)
    try:
        listing = client.get("/api/operations/work-orders", params={"asset_id": asset["id"]}).json()
        assert isinstance(listing, list)
        assert any(row["id"] == created["id"] for row in listing)
    finally:
        _delete_work_order(created["id"])


def test_patch_updates_allowed_fields(client: TestClient) -> None:
    asset = _pick_asset(client)
    created = _post_work_order(client, asset)
    try:
        response = client.patch(
            f"/api/work-orders/{created['id']}",
            json={"title": "Updated title", "priority": "critical"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["title"] == "Updated title"
        assert body["priority"] == "critical"
    finally:
        _delete_work_order(created["id"])


def test_technician_assignment_validates_existence(client: TestClient) -> None:
    asset = _pick_asset(client)
    created = _post_work_order(client, asset)
    try:
        response = client.patch(
            f"/api/work-orders/{created['id']}",
            json={"assigned_technician_id": "TEC-DOES-NOT-EXIST"},
        )
        assert response.status_code == 404
    finally:
        _delete_work_order(created["id"])


def test_valid_status_transition_creates_event(client: TestClient) -> None:
    asset = _pick_asset(client)
    created = _post_work_order(client, asset)
    try:
        response = client.patch(
            f"/api/work-orders/{created['id']}/status",
            json={"status": "in_progress", "actor": "operator-1", "notes": "Starting work"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "in_progress"
        assert body["started_at"] is not None
        assert len(body["events"]) == 1
        assert body["events"][0]["from_status"] == "open"
        assert body["events"][0]["to_status"] == "in_progress"
    finally:
        _delete_work_order(created["id"])


def test_invalid_status_transition_rejected(client: TestClient) -> None:
    asset = _pick_asset(client)
    created = _post_work_order(client, asset)
    try:
        response = client.patch(
            f"/api/work-orders/{created['id']}/status",
            json={"status": "closed"},
        )
        assert response.status_code == 422
    finally:
        _delete_work_order(created["id"])


def test_assigned_transition_requires_technician(client: TestClient) -> None:
    asset = _pick_asset(client)
    created = _post_work_order(client, asset)
    try:
        response = client.patch(
            f"/api/work-orders/{created['id']}/status",
            json={"status": "assigned"},
        )
        assert response.status_code == 422
    finally:
        _delete_work_order(created["id"])


def test_assigned_transition_with_technician(client: TestClient) -> None:
    asset = _pick_asset(client)
    tech_id = _create_technician(asset["zone"])
    created = _post_work_order(client, asset)
    try:
        response = client.patch(
            f"/api/work-orders/{created['id']}/status",
            json={
                "status": "assigned",
                "assigned_technician_id": tech_id,
                "actor": "dispatcher-1",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "assigned"
        assert body["assigned_technician_id"] == tech_id
        assert body["events"][-1]["from_status"] == "open"
        assert body["events"][-1]["to_status"] == "assigned"
        assert body["events"][-1]["actor"] == "dispatcher-1"
    finally:
        _delete_work_order(created["id"])
        _delete_technician(tech_id)


def test_assigned_to_in_progress_transition(client: TestClient) -> None:
    asset = _pick_asset(client)
    tech_id = _create_technician(asset["zone"])
    created = _post_work_order(client, asset)
    wo_id = created["id"]
    try:
        client.patch(
            f"/api/work-orders/{wo_id}/status",
            json={
                "status": "assigned",
                "assigned_technician_id": tech_id,
                "actor": "operations-ui",
            },
        )
        response = client.patch(
            f"/api/work-orders/{wo_id}/status",
            json={"status": "in_progress", "actor": "operations-ui"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "in_progress"
        assert body["assigned_technician_id"] == tech_id
        assert body["started_at"] is not None
        assert len(body["events"]) == 2
        assert body["events"][-1]["from_status"] == "assigned"
        assert body["events"][-1]["to_status"] == "in_progress"
    finally:
        _delete_work_order(wo_id)
        _delete_technician(tech_id)


def test_invalid_technician_on_assigned_transition(client: TestClient) -> None:
    asset = _pick_asset(client)
    created = _post_work_order(client, asset)
    try:
        response = client.patch(
            f"/api/work-orders/{created['id']}/status",
            json={
                "status": "assigned",
                "assigned_technician_id": "TEC-DOES-NOT-EXIST",
                "actor": "operations-ui",
            },
        )
        assert response.status_code == 404
    finally:
        _delete_work_order(created["id"])


def test_completed_sets_completed_at_and_notes(client: TestClient) -> None:
    asset = _pick_asset(client)
    created = _post_work_order(client, asset)
    wo_id = created["id"]
    try:
        client.patch(
            f"/api/work-orders/{wo_id}/status",
            json={"status": "in_progress"},
        )
        response = client.patch(
            f"/api/work-orders/{wo_id}/status",
            json={
                "status": "completed",
                "completion_notes": "Pump restored to nominal pressure",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        assert body["completed_at"] is not None
        assert body["completion_notes"] == "Pump restored to nominal pressure"
    finally:
        _delete_work_order(wo_id)


def test_closed_sets_closed_at_and_verification_notes(client: TestClient) -> None:
    asset = _pick_asset(client)
    created = _post_work_order(client, asset)
    wo_id = created["id"]
    try:
        client.patch(f"/api/work-orders/{wo_id}/status", json={"status": "in_progress"})
        client.patch(f"/api/work-orders/{wo_id}/status", json={"status": "completed"})
        response = client.patch(
            f"/api/work-orders/{wo_id}/status",
            json={
                "status": "closed",
                "verification_notes": "Verified by control room",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "closed"
        assert body["closed_at"] is not None
        assert body["verification_notes"] == "Verified by control room"
        assert len(body["events"]) == 3
    finally:
        _delete_work_order(wo_id)


def test_recommendation_snapshot_stored_unchanged(client: TestClient) -> None:
    asset = _pick_asset(client)
    snapshot = {
        "affected_asset": asset["id"],
        "affected_zone": asset["zone"],
        "priority": "critical",
        "title": "Pump failure contingency",
        "description": "Pressure degrading",
        "recommendation": "Activate standby pump",
        "confidence": 0.96,
        "rule_code": "PUMP_FAILURE",
        "scenario_key": "pump_failure",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    created = _post_work_order(
        client,
        asset,
        recommendation_id="rec-api-test-001",
        recommendation_snapshot=snapshot,
        source="recommendation",
    )
    try:
        assert created["recommendation_id"] == "rec-api-test-001"
        assert created["recommendation_snapshot"] == snapshot
        fetched = client.get(f"/api/work-orders/{created['id']}").json()
        assert fetched["recommendation_snapshot"] == snapshot
    finally:
        _delete_work_order(created["id"])


def test_work_order_does_not_mutate_telemetry_or_scenario(client: TestClient) -> None:
    asset = _pick_asset(client)
    asset_id = asset["id"]
    before_scenario = client.get("/api/scenarios/current").json()
    before_tel = client.get("/api/telemetry/latest", params={"utility": "water"}).json()
    before_metrics = {
        m["metric_key"]: (m["value"], m["recorded_at"])
        for row in before_tel
        if row.get("asset_id") == asset_id
        for m in row.get("metrics", [])
    }

    with (
        patch("app.domain.repositories.telemetry_repository.TelemetryRepository.add") as add_mock,
        patch("app.scenarios.engine.ScenarioEngine.select_scenario") as select_mock,
    ):
        created = _post_work_order(client, asset)
        wo_id = created["id"]
        try:
            client.patch(f"/api/work-orders/{wo_id}/status", json={"status": "in_progress"})
            client.patch(f"/api/work-orders/{wo_id}/status", json={"status": "completed"})
            client.patch(f"/api/work-orders/{wo_id}/status", json={"status": "closed"})
        finally:
            _delete_work_order(wo_id)

        add_mock.assert_not_called()
        select_mock.assert_not_called()

    after_scenario = client.get("/api/scenarios/current").json()
    after_tel = client.get("/api/telemetry/latest", params={"utility": "water"}).json()
    after_metrics = {
        m["metric_key"]: (m["value"], m["recorded_at"])
        for row in after_tel
        if row.get("asset_id") == asset_id
        for m in row.get("metrics", [])
    }
    assert after_scenario["key"] == before_scenario["key"]
    assert after_scenario["intensity"] == before_scenario["intensity"]
    # Live simulator may tick between reads; only assert scenario unchanged above.


def _recommendation_payload(asset: dict, rec_id: str | None = None) -> dict:
    rec_id = rec_id or f"rec-7e-test-{uuid.uuid4().hex[:8]}"
    snapshot = {
        "id": rec_id,
        "priority": "critical",
        "severity": "critical",
        "title": "Phase 7E recommendation test",
        "description": "Pressure degrading on primary pump",
        "affected_asset": asset["id"],
        "affected_zone": asset["zone"],
        "recommendation": "Activate standby pump",
        "estimated_business_impact": "Service disruption risk",
        "confidence": 0.91,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rule_code": "PUMP_FAILURE",
        "scenario_key": "pump_failure",
    }
    return {
        "asset_id": asset["id"],
        "title": snapshot["title"],
        "description": snapshot["description"],
        "priority": "critical",
        "source": "recommendation",
        "recommendation_id": rec_id,
        "recommendation_snapshot": snapshot,
        "created_by": "ai-intelligence-ui",
    }


def test_create_work_order_from_recommendation_payload(client: TestClient) -> None:
    asset = _pick_asset(client)
    body = _recommendation_payload(asset)
    created = client.post("/api/work-orders", json=body)
    assert created.status_code == 201, created.text
    data = created.json()
    try:
        assert data["source"] == "recommendation"
        assert data["recommendation_id"] == body["recommendation_id"]
        assert data["recommendation_snapshot"] == body["recommendation_snapshot"]
        assert data["created_by"] == "ai-intelligence-ui"
        assert data["asset_id"] == asset["id"]
        assert data["utility"] == asset["utility"]
        assert data["zone_id"] == asset["zone"]
        assert data["priority"] == "critical"
        assert data["status"] == "open"
    finally:
        _delete_work_order(data["id"])


def test_duplicate_active_recommendation_rejected(client: TestClient) -> None:
    asset = _pick_asset(client)
    rec_id = f"rec-7e-dup-{uuid.uuid4().hex[:8]}"
    body = _recommendation_payload(asset, rec_id=rec_id)
    first = client.post("/api/work-orders", json=body)
    assert first.status_code == 201, first.text
    wo_id = first.json()["id"]
    try:
        duplicate = client.post("/api/work-orders", json=body)
        assert duplicate.status_code == 422
        assert "Active work order already exists" in duplicate.json()["detail"]
        assert wo_id in duplicate.json()["detail"]
    finally:
        _delete_work_order(wo_id)


def test_duplicate_allowed_after_work_order_closed(client: TestClient) -> None:
    asset = _pick_asset(client)
    rec_id = f"rec-7e-closed-{uuid.uuid4().hex[:8]}"
    body = _recommendation_payload(asset, rec_id=rec_id)
    first = client.post("/api/work-orders", json=body)
    assert first.status_code == 201, first.text
    wo_id = first.json()["id"]
    second_id: str | None = None
    try:
        client.patch(f"/api/work-orders/{wo_id}/status", json={"status": "in_progress"})
        client.patch(f"/api/work-orders/{wo_id}/status", json={"status": "completed"})
        client.patch(f"/api/work-orders/{wo_id}/status", json={"status": "closed"})

        second = client.post("/api/work-orders", json=body)
        assert second.status_code == 201, second.text
        assert second.json()["recommendation_id"] == rec_id
        second_id = second.json()["id"]
    finally:
        if second_id:
            _delete_work_order(second_id)
        _delete_work_order(wo_id)


def test_recommendation_work_order_appears_in_operations_list(client: TestClient) -> None:
    asset = _pick_asset(client)
    body = _recommendation_payload(asset)
    created = client.post("/api/work-orders", json=body).json()
    try:
        listing = client.get(
            "/api/operations/work-orders", params={"asset_id": asset["id"]}
        ).json()
        assert any(row["id"] == created["id"] for row in listing)
    finally:
        _delete_work_order(created["id"])


def test_recommendation_work_order_lifecycle_and_assignment_intact(client: TestClient) -> None:
    asset = _pick_asset(client)
    body = _recommendation_payload(asset)
    created = client.post("/api/work-orders", json=body).json()
    wo_id = created["id"]
    tech_id = _create_technician(asset["zone"])
    try:
        assigned = client.patch(
            f"/api/work-orders/{wo_id}/status",
            json={
                "status": "assigned",
                "assigned_technician_id": tech_id,
                "actor": "operations-ui",
            },
        )
        assert assigned.status_code == 200
        assert assigned.json()["assigned_technician_id"] == tech_id

        progressed = client.patch(
            f"/api/work-orders/{wo_id}/status",
            json={"status": "in_progress", "actor": "operations-ui"},
        )
        assert progressed.status_code == 200
        assert progressed.json()["status"] == "in_progress"
    finally:
        _delete_work_order(wo_id)
        _delete_technician(tech_id)


def test_live_api_smoke_flow(client: TestClient) -> None:
    """End-to-end: asset → create → get → transition → events."""
    asset = _pick_asset(client)
    created = _post_work_order(
        client,
        asset,
        title="Phase 7B smoke test",
        description="Smoke test work order",
    )
    wo_id = created["id"]
    try:
        detail = client.get(f"/api/work-orders/{wo_id}").json()
        assert detail["asset_id"] == asset["id"]

        progressed = client.patch(
            f"/api/work-orders/{wo_id}/status",
            json={"status": "in_progress", "actor": "smoke-test"},
        ).json()
        assert progressed["status"] == "in_progress"
        assert progressed["events"]

        final = client.get(f"/api/work-orders/{wo_id}").json()
        assert final["events"][0]["actor"] == "smoke-test"
    finally:
        _delete_work_order(wo_id)
