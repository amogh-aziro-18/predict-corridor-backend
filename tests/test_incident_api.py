"""Incident API, lifecycle, and audit tests (Phase 11A)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.domain.repositories import IncidentRepository
from app.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _pick_asset(client: TestClient) -> dict:
    assets = client.get("/api/assets", params={"utility": "water"}).json()
    if not assets:
        pytest.skip("No water assets seeded")
    return assets[0]


def _pick_zone(client: TestClient) -> str:
    zones = client.get("/api/zones").json()
    if not zones:
        pytest.skip("No zones seeded")
    return zones[0]["id"]


def _delete_incident(incident_id: str) -> None:
    db = SessionLocal()
    try:
        row = IncidentRepository(db).get(incident_id)
        if row:
            IncidentRepository(db).delete(row)
    finally:
        db.close()


def _post_incident(client: TestClient, **body: object) -> dict:
    payload = {
        "title": f"Incident test {uuid.uuid4().hex[:6]}",
        "severity": "high",
        "source": "manual",
        **body,
    }
    response = client.post("/api/incidents", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_openapi_includes_incident_routes() -> None:
    paths = app.openapi()["paths"]
    assert "/api/incidents" in paths
    assert "/api/incidents/{incident_id}" in paths
    assert "/api/incidents/{incident_id}/status" in paths


def test_create_manual_corridor_incident(client: TestClient) -> None:
    created = _post_incident(
        client,
        scope="corridor",
        utility_kind="water",
        description="Corridor-wide pressure concern",
    )
    try:
        assert created["status"] == "open"
        assert created["scope"] == "corridor"
        assert created["zone_id"] is None
        assert created["asset_id"] is None
        assert created["utility_kind"] == "water"
        assert created["source"] == "manual"
    finally:
        _delete_incident(created["id"])


def test_create_manual_zone_incident(client: TestClient) -> None:
    zone_id = _pick_zone(client)
    created = _post_incident(
        client,
        scope="zone",
        zone_id=zone_id,
        utility_kind="water",
    )
    try:
        assert created["scope"] == "zone"
        assert created["zone_id"] == zone_id
        assert created["asset_id"] is None
        assert created["utility_kind"] == "water"
    finally:
        _delete_incident(created["id"])


def test_create_asset_incident(client: TestClient) -> None:
    asset = _pick_asset(client)
    created = _post_incident(client, scope="asset", asset_id=asset["id"])
    try:
        assert created["scope"] == "asset"
        assert created["asset_id"] == asset["id"]
        assert created["zone_id"] == asset["zone"]
        assert created["utility_kind"] == asset["utility"]
    finally:
        _delete_incident(created["id"])


def test_asset_scope_derives_utility_and_zone(client: TestClient) -> None:
    asset = _pick_asset(client)
    created = _post_incident(
        client,
        scope="asset",
        asset_id=asset["id"],
        # Client may omit utility/zone — server derives
    )
    try:
        assert created["utility_kind"] == asset["utility"]
        assert created["zone_id"] == asset["zone"]
    finally:
        _delete_incident(created["id"])


def test_asset_scope_rejects_conflicting_utility(client: TestClient) -> None:
    asset = _pick_asset(client)
    response = client.post(
        "/api/incidents",
        json={
            "title": "Conflict utility",
            "severity": "medium",
            "scope": "asset",
            "asset_id": asset["id"],
            "utility_kind": "power",
            "source": "manual",
        },
    )
    assert response.status_code == 422
    assert "conflicts" in response.json()["detail"]


def test_invalid_scope_combinations_rejected(client: TestClient) -> None:
    zone_id = _pick_zone(client)
    asset = _pick_asset(client)

    corridor_with_zone = client.post(
        "/api/incidents",
        json={
            "title": "Bad corridor",
            "severity": "low",
            "scope": "corridor",
            "zone_id": zone_id,
            "utility_kind": "water",
            "source": "manual",
        },
    )
    assert corridor_with_zone.status_code == 422

    zone_with_asset = client.post(
        "/api/incidents",
        json={
            "title": "Bad zone",
            "severity": "low",
            "scope": "zone",
            "zone_id": zone_id,
            "asset_id": asset["id"],
            "utility_kind": "water",
            "source": "manual",
        },
    )
    assert zone_with_asset.status_code == 422

    zone_missing_id = client.post(
        "/api/incidents",
        json={
            "title": "Missing zone",
            "severity": "low",
            "scope": "zone",
            "utility_kind": "water",
            "source": "manual",
        },
    )
    assert zone_missing_id.status_code == 422


def test_explicit_utility_required_for_corridor_and_zone(client: TestClient) -> None:
    zone_id = _pick_zone(client)

    corridor = client.post(
        "/api/incidents",
        json={
            "title": "No utility corridor",
            "severity": "high",
            "scope": "corridor",
            "source": "manual",
        },
    )
    assert corridor.status_code == 422
    assert "utility_kind is required" in corridor.json()["detail"]

    zone = client.post(
        "/api/incidents",
        json={
            "title": "No utility zone",
            "severity": "high",
            "scope": "zone",
            "zone_id": zone_id,
            "source": "manual",
        },
    )
    assert zone.status_code == 422
    assert "utility_kind is required" in zone.json()["detail"]


def test_recommendation_escalation_and_snapshot(client: TestClient) -> None:
    rec_id = f"rec-inc-{uuid.uuid4().hex[:8]}"
    snapshot = {
        "id": rec_id,
        "title": "Escalate pump check",
        "severity": "high",
        "utility": "water",
    }
    created = _post_incident(
        client,
        scope="corridor",
        utility_kind="water",
        source="recommendation",
        recommendation_id=rec_id,
        recommendation_snapshot=snapshot,
    )
    try:
        assert created["source"] == "recommendation"
        assert created["recommendation_id"] == rec_id
        assert created["recommendation_snapshot"] == snapshot
    finally:
        _delete_incident(created["id"])


def test_recommendation_requires_id_and_snapshot(client: TestClient) -> None:
    missing_id = client.post(
        "/api/incidents",
        json={
            "title": "Rec missing id",
            "severity": "high",
            "scope": "corridor",
            "utility_kind": "water",
            "source": "recommendation",
            "recommendation_snapshot": {"title": "x"},
        },
    )
    assert missing_id.status_code == 422

    missing_snap = client.post(
        "/api/incidents",
        json={
            "title": "Rec missing snap",
            "severity": "high",
            "scope": "corridor",
            "utility_kind": "water",
            "source": "recommendation",
            "recommendation_id": "rec-x",
        },
    )
    assert missing_snap.status_code == 422


def test_duplicate_active_recommendation_protection(client: TestClient) -> None:
    rec_id = f"rec-dup-{uuid.uuid4().hex[:8]}"
    snapshot = {"id": rec_id, "title": "Dup check"}
    first = _post_incident(
        client,
        scope="corridor",
        utility_kind="water",
        source="recommendation",
        recommendation_id=rec_id,
        recommendation_snapshot=snapshot,
    )
    try:
        duplicate = client.post(
            "/api/incidents",
            json={
                "title": "Dup incident",
                "severity": "high",
                "scope": "corridor",
                "utility_kind": "water",
                "source": "recommendation",
                "recommendation_id": rec_id,
                "recommendation_snapshot": snapshot,
            },
        )
        assert duplicate.status_code == 422
        assert "Active incident already exists" in duplicate.json()["detail"]

        # Close first, then allow new
        closed = client.patch(
            f"/api/incidents/{first['id']}/status",
            json={"status": "closed", "notes": "Dismissed duplicate test"},
        )
        assert closed.status_code == 200

        second = _post_incident(
            client,
            scope="corridor",
            utility_kind="water",
            source="recommendation",
            recommendation_id=rec_id,
            recommendation_snapshot=snapshot,
        )
        try:
            assert second["recommendation_id"] == rec_id
            assert second["id"] != first["id"]
        finally:
            _delete_incident(second["id"])
    finally:
        _delete_incident(first["id"])


def test_create_audit_event(client: TestClient) -> None:
    created = _post_incident(client, scope="corridor", utility_kind="water")
    try:
        detail = client.get(f"/api/incidents/{created['id']}").json()
        assert len(detail["events"]) >= 1
        assert detail["events"][0]["from_status"] is None
        assert detail["events"][0]["to_status"] == "open"
    finally:
        _delete_incident(created["id"])


def test_lifecycle_happy_path_and_timestamps(client: TestClient) -> None:
    created = _post_incident(client, scope="corridor", utility_kind="water")
    incident_id = created["id"]
    try:
        ack = client.patch(
            f"/api/incidents/{incident_id}/status",
            json={"status": "acknowledged", "actor": "ops"},
        )
        assert ack.status_code == 200
        assert ack.json()["status"] == "acknowledged"
        assert ack.json()["acknowledged_at"] is not None

        progress = client.patch(
            f"/api/incidents/{incident_id}/status",
            json={"status": "in_progress"},
        )
        assert progress.status_code == 200
        assert progress.json()["status"] == "in_progress"

        resolved = client.patch(
            f"/api/incidents/{incident_id}/status",
            json={"status": "resolved"},
        )
        assert resolved.status_code == 200
        assert resolved.json()["status"] == "resolved"
        assert resolved.json()["resolved_at"] is not None

        closed = client.patch(
            f"/api/incidents/{incident_id}/status",
            json={"status": "closed"},
        )
        assert closed.status_code == 200
        body = closed.json()
        assert body["status"] == "closed"
        assert body["closed_at"] is not None
        assert len(body["events"]) >= 5  # create + 4 transitions
    finally:
        _delete_incident(incident_id)


def test_open_to_closed_requires_notes(client: TestClient) -> None:
    created = _post_incident(client, scope="corridor", utility_kind="water")
    try:
        missing = client.patch(
            f"/api/incidents/{created['id']}/status",
            json={"status": "closed"},
        )
        assert missing.status_code == 422
        assert "notes are required" in missing.json()["detail"]

        ok = client.patch(
            f"/api/incidents/{created['id']}/status",
            json={"status": "closed", "notes": "False alarm"},
        )
        assert ok.status_code == 200
        assert ok.json()["status"] == "closed"
        assert ok.json()["closed_at"] is not None
    finally:
        _delete_incident(created["id"])


def test_invalid_transitions_rejected(client: TestClient) -> None:
    created = _post_incident(client, scope="corridor", utility_kind="water")
    try:
        bad = client.patch(
            f"/api/incidents/{created['id']}/status",
            json={"status": "resolved"},
        )
        assert bad.status_code == 422
        assert "Invalid status transition" in bad.json()["detail"]
    finally:
        _delete_incident(created["id"])


def test_closed_is_terminal(client: TestClient) -> None:
    created = _post_incident(client, scope="corridor", utility_kind="water")
    try:
        client.patch(
            f"/api/incidents/{created['id']}/status",
            json={"status": "closed", "notes": "done"},
        )
        again = client.patch(
            f"/api/incidents/{created['id']}/status",
            json={"status": "acknowledged"},
        )
        assert again.status_code == 422
        assert "Invalid status transition" in again.json()["detail"]
    finally:
        _delete_incident(created["id"])


def test_list_filters(client: TestClient) -> None:
    created = _post_incident(
        client,
        scope="corridor",
        utility_kind="water",
        severity="critical",
    )
    try:
        listing = client.get(
            "/api/incidents",
            params={
                "status": "open",
                "severity": "critical",
                "utility_kind": "water",
                "scope": "corridor",
            },
        ).json()
        ids = {row["id"] for row in listing}
        assert created["id"] in ids
    finally:
        _delete_incident(created["id"])


def test_detail_includes_events(client: TestClient) -> None:
    created = _post_incident(client, scope="corridor", utility_kind="water")
    try:
        client.patch(
            f"/api/incidents/{created['id']}/status",
            json={"status": "acknowledged"},
        )
        detail = client.get(f"/api/incidents/{created['id']}").json()
        assert len(detail["events"]) == 2
        assert detail["events"][0]["to_status"] == "open"
        assert detail["events"][1]["from_status"] == "open"
        assert detail["events"][1]["to_status"] == "acknowledged"
    finally:
        _delete_incident(created["id"])


def test_patch_title_description(client: TestClient) -> None:
    created = _post_incident(client, scope="corridor", utility_kind="water")
    try:
        updated = client.patch(
            f"/api/incidents/{created['id']}",
            json={"title": "Updated title", "description": "Updated desc"},
        )
        assert updated.status_code == 200
        assert updated.json()["title"] == "Updated title"
        assert updated.json()["description"] == "Updated desc"
    finally:
        _delete_incident(created["id"])


def test_unknown_zone_returns_404(client: TestClient) -> None:
    response = client.post(
        "/api/incidents",
        json={
            "title": "Bad zone",
            "severity": "low",
            "scope": "zone",
            "zone_id": "zone-does-not-exist",
            "utility_kind": "water",
            "source": "manual",
        },
    )
    assert response.status_code == 404
