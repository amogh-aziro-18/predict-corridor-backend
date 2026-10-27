"""Phase 14.13 — GIS operational GeoJSON layers."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db.seed import seed_all
from app.main import app
from app.services.gis_operational_service import _operational_severity


def test_operational_severity_mapping() -> None:
    assert (
        _operational_severity(status="offline", risk="low", open_alert_max_severity=None)
        == "CRITICAL"
    )
    assert (
        _operational_severity(status="degraded", risk="low", open_alert_max_severity=None)
        == "DEGRADED"
    )
    assert (
        _operational_severity(status="operational", risk="low", open_alert_max_severity=None)
        == "HEALTHY"
    )
    assert (
        _operational_severity(
            status="operational", risk="low", open_alert_max_severity="critical"
        )
        == "CRITICAL"
    )


def test_gis_layers_endpoint_returns_geojson() -> None:
    seed_all()
    client = TestClient(app)
    res = client.get("/api/gis/layers")
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["corridor"]["display_name"] == "Delhi, India"
    assert body["corridor"]["latitude"] == 28.6139
    assert body["assets"]["type"] == "FeatureCollection"
    assert body["zones"]["type"] == "FeatureCollection"
    assert body["assets"]["meta"]["feature_count"] >= 1
    assert body["zones"]["meta"]["boundary_surveyed"] is False

    for feat in body["assets"]["features"]:
        assert feat["type"] == "Feature"
        assert feat["geometry"]["type"] == "Point"
        lon, lat = feat["geometry"]["coordinates"]
        assert 28.4 <= lat <= 28.8
        assert 77.1 <= lon <= 77.4
        props = feat["properties"]
        assert props["coordinate_source"] == "postgresql_asset_registry"
        assert props["utility_kind"] in {"water", "power", "gas", "communications"}
        assert "health" in props
        assert "operational_severity" in props
        assert "open_alert_count" in props

    utilities = {f["properties"]["utility_kind"] for f in body["assets"]["features"]}
    assert "water" in utilities
    assert "power" in utilities
    # Gas foundation (Phase 15.0) seeds real Gas assets — expect them on the map layer.
    assert "gas" in utilities
    # Communications (Phase 17.10) seeds POP/GW assets on the live GIS layer.
    assert "communications" in utilities


def test_gis_layers_utility_filter() -> None:
    seed_all()
    client = TestClient(app)
    water = client.get("/api/gis/layers", params={"utility": "water"}).json()
    power = client.get("/api/gis/layers", params={"utility": "power"}).json()
    gas = client.get("/api/gis/layers", params={"utility": "gas"}).json()
    assert all(f["properties"]["utility_kind"] == "water" for f in water["assets"]["features"])
    assert all(f["properties"]["utility_kind"] == "power" for f in power["assets"]["features"])
    assert all(f["properties"]["utility_kind"] == "gas" for f in gas["assets"]["features"])
    assert water["assets"]["meta"]["feature_count"] >= 1
    assert power["assets"]["meta"]["feature_count"] >= 1
    assert gas["assets"]["meta"]["feature_count"] == 12
    assert {f["properties"]["zone_id"] for f in gas["assets"]["features"]} == {
        "zone-a",
        "zone-b",
        "zone-c",
        "zone-d",
    }


def test_discovery_mentions_layers() -> None:
    client = TestClient(app)
    res = client.get("/api/gis")
    assert res.status_code == 200
    assert "/gis/layers" in res.json()["message"]
