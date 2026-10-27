"""Asset API tests — identity, filters, coordinates (uses live DB when available)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_openapi_includes_asset_routes() -> None:
    paths = app.openapi()["paths"]
    assert "/api/assets" in paths
    assert "/api/assets/{asset_id}" in paths


def test_list_assets_returns_registry(client: TestClient) -> None:
    response = client.get("/api/assets")
    assert response.status_code == 200
    rows = response.json()
    assert isinstance(rows, list)
    assert len(rows) >= 1
    sample = rows[0]
    assert "id" in sample
    assert "name" in sample
    assert "utility" in sample
    assert "zone" in sample
    assert "type" in sample
    # Coordinates may be null for future utilities; field must exist.
    assert "latitude" in sample
    assert "longitude" in sample


def test_asset_id_consistency_list_and_detail(client: TestClient) -> None:
    listing = client.get("/api/assets").json()
    asset_id = listing[0]["id"]
    detail = client.get(f"/api/assets/{asset_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["id"] == asset_id
    assert body["asset_id"] == asset_id


def test_missing_asset_returns_404(client: TestClient) -> None:
    response = client.get("/api/assets/DOES-NOT-EXIST-XYZ")
    assert response.status_code == 404


def test_utility_and_zone_filters(client: TestClient) -> None:
    water = client.get("/api/assets", params={"utility": "water"}).json()
    assert all(row["utility"] == "water" for row in water)
    if not water:
        pytest.skip("No water assets seeded")
    zone_id = water[0]["zone"]
    zoned = client.get("/api/assets", params={"utility": "water", "zone": zone_id}).json()
    assert all(row["zone"] == zone_id for row in zoned)


def test_asset_type_filter(client: TestClient) -> None:
    pumps = client.get(
        "/api/assets",
        params={"utility": "water", "asset_type": "pump_station"},
    ).json()
    assert all(row["type"] == "pump_station" for row in pumps)


def test_coordinates_are_numeric_or_null(client: TestClient) -> None:
    rows = client.get("/api/assets").json()
    for row in rows:
        lat, lon = row.get("latitude"), row.get("longitude")
        if lat is None and lon is None:
            continue
        assert isinstance(lat, (int, float))
        assert isinstance(lon, (int, float))
        assert -90 <= float(lat) <= 90
        assert -180 <= float(lon) <= 180
