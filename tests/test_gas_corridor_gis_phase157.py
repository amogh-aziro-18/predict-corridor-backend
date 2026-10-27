"""Phase 15.7 — Gas first-class in corridor dashboard + GIS composition."""

from __future__ import annotations

from collections import Counter
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.schemas.dashboard import (
    CommunicationsDashboardOut,
    GasDashboardOut,
    PowerDashboardOut,
    WaterDashboardOut,
)
from app.db.seed import seed_all
from app.main import app
from app.services.dashboard_service import (
    compose_corridor_posture,
    gas_condition_to_posture,
)


def test_gas_condition_to_posture_mapping() -> None:
    assert gas_condition_to_posture("healthy", api_status="healthy") == "HEALTHY"
    assert gas_condition_to_posture("warning", api_status="healthy") == "STABLE"
    assert gas_condition_to_posture("high_risk", api_status="healthy") == "DEGRADED"
    assert gas_condition_to_posture("critical", api_status="healthy") == "CRITICAL"
    assert gas_condition_to_posture("unavailable", api_status="healthy") == "UNAVAILABLE"
    assert gas_condition_to_posture("healthy", api_status="unreachable") == "UNAVAILABLE"


def test_compose_includes_three_utilities_worst_of() -> None:
    assert compose_corridor_posture(["HEALTHY", "STABLE", "CRITICAL"]) == "CRITICAL"
    assert compose_corridor_posture(["HEALTHY", "UNAVAILABLE", "DEGRADED"]) == "DEGRADED"
    assert compose_corridor_posture(["UNAVAILABLE", "UNAVAILABLE", "UNAVAILABLE"]) == "UNAVAILABLE"


def test_corridor_dashboard_includes_water_power_gas_communications() -> None:
    seed_all()
    client = TestClient(app)
    res = client.get("/api/dashboard/corridor")
    assert res.status_code == 200, res.text
    body = res.json()
    kinds = {u["utility"] for u in body["utilities"]}
    assert kinds == {"water", "power", "gas", "communications"}
    gas = next(u for u in body["utilities"] if u["utility"] == "gas")
    assert gas["overall_condition"] is not None
    assert isinstance(gas["overall_health"], (int, float))
    assert isinstance(gas["asset_count"], int)
    assert isinstance(gas["active_alerts"], int)
    comm = next(u for u in body["utilities"] if u["utility"] == "communications")
    assert comm["overall_condition"] is not None
    assert isinstance(comm["asset_count"], int)
    assert "Gas" in body["explanation"] or "gas" in body["explanation"].lower()
    assert (
        "Communications" in body["explanation"]
        or "communications" in body["explanation"].lower()
    )
    assert body["assessment_kind"] == "composed_utility_operational_posture"


def test_gas_corridor_condition_matches_gas_dashboard() -> None:
    seed_all()
    client = TestClient(app)
    gas = client.get("/api/dashboard/gas")
    corridor = client.get("/api/dashboard/corridor")
    assert gas.status_code == 200
    assert corridor.status_code == 200
    gas_body = gas.json()
    gas_snap = next(u for u in corridor.json()["utilities"] if u["utility"] == "gas")
    assert gas_snap["overall_condition"] == gas_body["overall_condition"]
    assert gas_snap["overall_health"] == gas_body["overall_health"]
    assert gas_snap["asset_count"] == gas_body["asset_count"]
    assert gas_snap["active_alerts"] == gas_body["active_alerts"]
    assert gas_snap["critical_assets"] == gas_body["critical_assets"]


def test_overall_posture_follows_worst_live_including_gas() -> None:
    """When Gas is the worst live utility, corridor overall equals Gas posture."""
    seed_all()
    client = TestClient(app)

    water = WaterDashboardOut(
        overall_health=96.0,
        critical_assets=0,
        active_alerts=0,
        zone_summary=[],
        asset_count=12,
        average_pressure=3.1,
        average_flow=120.0,
        utility="water",
        api_status="healthy",
    )
    power = PowerDashboardOut(
        overall_health=94.0,
        overall_condition="healthy",
        critical_assets=0,
        active_alerts=0,
        zone_summary=[],
        asset_count=12,
        utility="power",
        api_status="healthy",
    )
    gas = GasDashboardOut(
        overall_health=40.0,
        overall_condition="critical",
        critical_assets=2,
        active_alerts=5,
        zone_summary=[],
        asset_count=12,
        utility="gas",
        api_status="healthy",
    )

    with (
        patch(
            "app.services.dashboard_service.DashboardService.water_dashboard",
            return_value=water,
        ),
        patch(
            "app.services.dashboard_service.DashboardService.power_dashboard",
            return_value=power,
        ),
        patch(
            "app.services.dashboard_service.DashboardService.gas_dashboard",
            return_value=gas,
        ),
    ):
        body = client.get("/api/dashboard/corridor").json()

    assert body["overall_posture"] == "CRITICAL"
    gas_snap = next(u for u in body["utilities"] if u["utility"] == "gas")
    assert gas_snap["posture"] == "CRITICAL"
    assert any("Gas posture CRITICAL" in d for d in body["drivers"])


def test_gas_unavailable_does_not_fabricate_values() -> None:
    seed_all()
    client = TestClient(app)

    water = WaterDashboardOut(
        overall_health=96.0,
        critical_assets=0,
        active_alerts=0,
        zone_summary=[],
        asset_count=12,
        average_pressure=3.1,
        average_flow=120.0,
        utility="water",
        api_status="healthy",
    )
    power = PowerDashboardOut(
        overall_health=94.0,
        overall_condition="healthy",
        critical_assets=0,
        active_alerts=0,
        zone_summary=[],
        asset_count=12,
        utility="power",
        api_status="healthy",
    )
    gas = GasDashboardOut(
        overall_health=0.0,
        overall_condition="unavailable",
        critical_assets=0,
        active_alerts=0,
        zone_summary=[],
        asset_count=0,
        utility="gas",
        api_status="unreachable",
    )
    # Isolate Communications so live Comm stress cannot change overall posture
    # for this Gas-unavailable composition case.
    communications = CommunicationsDashboardOut(
        overall_health=98.0,
        overall_condition="healthy",
        critical_assets=0,
        active_alerts=0,
        zone_summary=[],
        asset_count=8,
        utility="communications",
        api_status="healthy",
    )

    with (
        patch(
            "app.services.dashboard_service.DashboardService.water_dashboard",
            return_value=water,
        ),
        patch(
            "app.services.dashboard_service.DashboardService.power_dashboard",
            return_value=power,
        ),
        patch(
            "app.services.dashboard_service.DashboardService.gas_dashboard",
            return_value=gas,
        ),
        patch(
            "app.services.dashboard_service.DashboardService.communications_dashboard",
            return_value=communications,
        ),
    ):
        body = client.get("/api/dashboard/corridor").json()

    gas_snap = next(u for u in body["utilities"] if u["utility"] == "gas")
    assert gas_snap["available"] is False
    assert gas_snap["posture"] == "UNAVAILABLE"
    assert gas_snap["api_status"] == "unreachable"
    # Overall still derives from healthy Water/Power/Communications — not
    # fabricated from Gas unavailability.
    assert body["overall_posture"] == "HEALTHY"
    assert body["overall_posture"] != "UNAVAILABLE"


def test_gis_gas_layers_twelve_assets_four_zones() -> None:
    seed_all()
    client = TestClient(app)
    gas = client.get("/api/gis/layers", params={"utility": "gas"})
    assert gas.status_code == 200, gas.text
    body = gas.json()
    features = body["assets"]["features"]
    assert body["assets"]["meta"]["feature_count"] == 12
    assert len(features) == 12
    assert all(f["properties"]["utility_kind"] == "gas" for f in features)
    zones = Counter(f["properties"]["zone_id"] for f in features)
    assert set(zones) == {"zone-a", "zone-b", "zone-c", "zone-d"}
    assert all(zones[z] == 3 for z in zones)
    types = {f["properties"]["asset_type"] for f in features}
    assert types == {"gas_compressor", "gas_regulator_station", "gas_feeder"}
    # Operational IDs retain CMP / REG / GFDR prefixes (not hardcoded in GIS UI).
    ids = {f["properties"]["asset_id"] for f in features}
    assert any(i.startswith("CMP-") for i in ids)
    assert any(i.startswith("REG-") for i in ids)
    assert any(i.startswith("GFDR-") for i in ids)
    for feat in features:
        lon, lat = feat["geometry"]["coordinates"]
        assert isinstance(lat, (int, float)) and isinstance(lon, (int, float))
        assert feat["properties"]["coordinate_source"] == "postgresql_asset_registry"


def test_gis_water_and_power_still_work() -> None:
    seed_all()
    client = TestClient(app)
    water = client.get("/api/gis/layers", params={"utility": "water"}).json()
    power = client.get("/api/gis/layers", params={"utility": "power"}).json()
    assert water["assets"]["meta"]["feature_count"] >= 1
    assert power["assets"]["meta"]["feature_count"] >= 1
    assert all(f["properties"]["utility_kind"] == "water" for f in water["assets"]["features"])
    assert all(f["properties"]["utility_kind"] == "power" for f in power["assets"]["features"])


def test_gas_assets_api_zone_distribution() -> None:
    seed_all()
    client = TestClient(app)
    assets = client.get("/api/assets", params={"utility": "gas"}).json()
    assert len(assets) == 12
    zones = Counter(a["zone"] for a in assets)
    assert set(zones) == {"zone-a", "zone-b", "zone-c", "zone-d"}
    assert all(zones[z] == 3 for z in zones)
    types = {a["type"] for a in assets}
    assert types == {"gas_compressor", "gas_regulator_station", "gas_feeder"}


def test_gas_alerts_and_recommendations_endpoints_unchanged() -> None:
    """Composition phase must not break existing Gas alert/recommendation APIs."""
    seed_all()
    client = TestClient(app)
    alerts = client.get("/api/alerts", params={"utility_kind": "gas", "status": "open"})
    recs = client.get("/api/recommendations")
    assert alerts.status_code == 200
    assert recs.status_code == 200
    assert isinstance(alerts.json(), list)
    assert isinstance(recs.json(), list)


def test_gas_technician_catalog_unchanged() -> None:
    seed_all()
    client = TestClient(app)
    gas = client.get("/api/technicians", params={"utility": "gas"}).json()
    ids = {t["id"] for t in gas}
    assert {"TEC-G-01", "TEC-G-02", "TEC-G-03", "TEC-G-04", "TEC-M-01"}.issubset(ids)
