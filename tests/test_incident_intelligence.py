"""Incident intelligence composition tests (Phase 11B)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
import pytest
from fastapi.testclient import TestClient

from app.api.schemas.environment_impact import (
    EnvironmentImpactAssessmentOut,
    EnvironmentImpactEvidenceOut,
)
from app.api.schemas.environment_signal import EnvironmentSignalScope
from app.api.schemas.resilience import (
    ResilienceAssessmentOut,
    ResilienceInputsAvailabilityOut,
    ResilienceScope,
)
from app.api.schemas.weather import WeatherLocationOut, WeatherProviderMetaOut
from app.api.schemas.weather_impact import (
    WeatherImpactAssessmentOut,
    WeatherImpactScope,
)
from app.core.errors import EnvironmentUnavailableError, WeatherUnavailableError
from app.db.session import SessionLocal
from app.domain.enums import TechnicianAvailability
from app.domain.models import Technician
from app.domain.repositories import IncidentRepository, TechnicianRepository
from app.main import app
from app.services.incident_intelligence_service import IncidentIntelligenceService

NOW = datetime.now(timezone.utc)


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


def _create_incident(client: TestClient, **body: object) -> dict:
    payload = {
        "title": f"Intel test {uuid.uuid4().hex[:6]}",
        "severity": "high",
        "source": "manual",
        **body,
    }
    response = client.post("/api/incidents", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _location(**overrides: Any) -> WeatherLocationOut:
    base = {
        "latitude": 0.0,
        "longitude": 0.0,
        "timezone": "UTC",
        "scope": "corridor",
        "zone_id": None,
        "asset_count": 0,
        "utility_kind": "water",
        "method": "test",
    }
    base.update(overrides)
    return WeatherLocationOut(**base)


def _meta(**overrides: Any) -> WeatherProviderMetaOut:
    base = {
        "provider": "test",
        "fetched_at": NOW,
        "cache_status": "fresh",
        "cache_expires_at": None,
        "stale": False,
    }
    base.update(overrides)
    return WeatherProviderMetaOut(**base)


def _wx_assessment(**overrides: Any) -> WeatherImpactAssessmentOut:
    base = {
        "location": _location(),
        "provider_meta": _meta(),
        "forecast_provider_meta": _meta(),
        "assessed_at": NOW,
        "scope": WeatherImpactScope.CORRIDOR,
        "zone_id": None,
        "forecast_hours": 24,
        "overall_severity": "medium",
        "signals": [],
        "static_metadata_context": None,
    }
    base.update(overrides)
    return WeatherImpactAssessmentOut(**base)


def _env_assessment(**overrides: Any) -> EnvironmentImpactAssessmentOut:
    base = {
        "location": _location(),
        "provider_meta": _meta(provider="test_aq"),
        "assessed_at": NOW,
        "scope": EnvironmentSignalScope.CORRIDOR,
        "zone_id": None,
        "utility_kind": "water",
        "forecast_hours": 24,
        "observation_kinds": [],
        "overall_severity": "low",
        "signals": [],
        "evidence": EnvironmentImpactEvidenceOut(
            note="test",
            threshold_basis="test",
            aqi_policy="us_aqi only",
        ),
        "note": None,
    }
    base.update(overrides)
    return EnvironmentImpactAssessmentOut(**base)


def _res_assessment(**overrides: Any) -> ResilienceAssessmentOut:
    base = {
        "scope": ResilienceScope.CORRIDOR,
        "zone_id": None,
        "utility_kind": "water",
        "overall_posture": "medium",
        "overall_severity": "medium",
        "drivers": [],
        "evidence": ["test"],
        "assessed_at": datetime.now(timezone.utc),
        "forecast_hours": 24,
        "inputs_available": ResilienceInputsAvailabilityOut(
            weather_available=True,
            environment_available=True,
            scenario_available=True,
            assets_available=True,
        ),
        "inputs_stale": False,
        "note": "test",
    }
    base.update(overrides)
    return ResilienceAssessmentOut(**base)


class StubWeather:
    def __init__(self, assessment: WeatherImpactAssessmentOut | None = None, *, fail: bool = False):
        self.assessment = assessment or _wx_assessment()
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def assess(self, *, zone_id: str | None = None, hours: int = 48) -> WeatherImpactAssessmentOut:
        self.calls.append({"zone_id": zone_id, "hours": hours})
        if self.fail:
            raise WeatherUnavailableError("stub weather down")
        return self.assessment


class StubEnv:
    def __init__(
        self, assessment: EnvironmentImpactAssessmentOut | None = None, *, fail: bool = False
    ):
        self.assessment = assessment or _env_assessment()
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def assess(self, **kwargs: Any) -> EnvironmentImpactAssessmentOut:
        self.calls.append(kwargs)
        if self.fail:
            raise EnvironmentUnavailableError("stub env down")
        return self.assessment


class StubResilience:
    def __init__(self, assessment: ResilienceAssessmentOut | None = None):
        self.assessment = assessment or _res_assessment()
        self.calls: list[dict[str, Any]] = []

    def assess(self, **kwargs: Any) -> ResilienceAssessmentOut:
        self.calls.append(kwargs)
        return self.assessment


def _intel_service(
    db,
    *,
    weather=None,
    environment=None,
    resilience=None,
) -> IncidentIntelligenceService:
    return IncidentIntelligenceService(
        db,
        weather_impact_service=weather or StubWeather(),
        environment_impact_service=environment or StubEnv(),
        resilience_assessment_service=resilience or StubResilience(),
    )


def test_openapi_includes_intelligence_route() -> None:
    paths = app.openapi()["paths"]
    assert "/api/incidents/{incident_id}/intelligence" in paths


def test_corridor_intelligence(client: TestClient) -> None:
    created = _create_incident(client, scope="corridor", utility_kind="water")
    try:
        db = SessionLocal()
        try:
            wx = StubWeather(_wx_assessment(scope=WeatherImpactScope.CORRIDOR, zone_id=None))
            env = StubEnv()
            res = StubResilience()
            out = _intel_service(db, weather=wx, environment=env, resilience=res).get_context(
                created["id"], hours=24
            )
        finally:
            db.close()

        assert out.incident.id == created["id"]
        assert out.assessment_scope.incident_scope == "corridor"
        assert out.assessment_scope.weather_env_resilience_zone_id is None
        assert out.assessment_scope.utility_kind == "water"
        assert out.weather is not None
        assert out.environment is not None
        assert out.resilience is not None
        assert out.inputs_available.weather is True
        assert out.inputs_available.environment is True
        assert out.inputs_available.resilience is True
        assert out.recommended_action.summary is None
        assert wx.calls[0]["zone_id"] is None
        assert env.calls[0]["zone_id"] is None
        assert res.calls[0]["zone_id"] is None
        assert all(a.utility == "water" for a in out.assets)
    finally:
        _delete_incident(created["id"])


def test_zone_intelligence(client: TestClient) -> None:
    zone_id = _pick_zone(client)
    created = _create_incident(
        client, scope="zone", zone_id=zone_id, utility_kind="water"
    )
    try:
        db = SessionLocal()
        try:
            wx = StubWeather()
            env = StubEnv()
            res = StubResilience(
                _res_assessment(scope=ResilienceScope.ZONE, zone_id=zone_id)
            )
            out = _intel_service(db, weather=wx, environment=env, resilience=res).get_context(
                created["id"]
            )
        finally:
            db.close()

        assert out.assessment_scope.incident_scope == "zone"
        assert out.assessment_scope.weather_env_resilience_zone_id == zone_id
        assert wx.calls[0]["zone_id"] == zone_id
        assert env.calls[0]["utility_kind"] == "water"
        assert all(a.zone == zone_id for a in out.assets)
    finally:
        _delete_incident(created["id"])


def test_asset_intelligence_maps_to_asset_zone(client: TestClient) -> None:
    asset = _pick_asset(client)
    created = _create_incident(client, scope="asset", asset_id=asset["id"])
    try:
        db = SessionLocal()
        try:
            wx = StubWeather()
            env = StubEnv()
            res = StubResilience()
            out = _intel_service(db, weather=wx, environment=env, resilience=res).get_context(
                created["id"]
            )
        finally:
            db.close()

        assert out.assessment_scope.incident_scope == "asset"
        assert out.assessment_scope.weather_env_resilience_zone_id == asset["zone"]
        assert out.assessment_scope.utility_kind == asset["utility"]
        assert "centroid" in out.assessment_scope.note.lower() or "zone-level" in out.assessment_scope.note.lower()
        assert wx.calls[0]["zone_id"] == asset["zone"]
        assert len(out.assets) == 1
        assert out.assets[0].id == asset["id"]
    finally:
        _delete_incident(created["id"])


def test_utility_propagation_on_corridor(client: TestClient) -> None:
    created = _create_incident(client, scope="corridor", utility_kind="water")
    try:
        db = SessionLocal()
        try:
            env = StubEnv()
            res = StubResilience()
            _intel_service(db, environment=env, resilience=res).get_context(created["id"])
        finally:
            db.close()
        assert env.calls[0]["utility_kind"] == "water"
        assert res.calls[0]["utility_kind"] == "water"
    finally:
        _delete_incident(created["id"])


def test_weather_unavailable(client: TestClient) -> None:
    created = _create_incident(client, scope="corridor", utility_kind="water")
    try:
        db = SessionLocal()
        try:
            out = _intel_service(
                db, weather=StubWeather(fail=True), environment=StubEnv(), resilience=StubResilience()
            ).get_context(created["id"])
        finally:
            db.close()
        assert out.weather is None
        assert out.inputs_available.weather is False
        assert out.environment is not None
        assert out.resilience is not None
    finally:
        _delete_incident(created["id"])


def test_environment_unavailable(client: TestClient) -> None:
    created = _create_incident(client, scope="corridor", utility_kind="water")
    try:
        db = SessionLocal()
        try:
            out = _intel_service(
                db, weather=StubWeather(), environment=StubEnv(fail=True), resilience=StubResilience()
            ).get_context(created["id"])
        finally:
            db.close()
        assert out.environment is None
        assert out.inputs_available.environment is False
        assert out.weather is not None
    finally:
        _delete_incident(created["id"])


def test_work_order_selection_for_asset(client: TestClient) -> None:
    asset = _pick_asset(client)
    wo = client.post(
        "/api/work-orders",
        json={
            "asset_id": asset["id"],
            "title": f"Intel WO {uuid.uuid4().hex[:6]}",
            "priority": "high",
            "source": "manual",
        },
    )
    assert wo.status_code == 201, wo.text
    wo_id = wo.json()["id"]
    created = _create_incident(client, scope="asset", asset_id=asset["id"])
    try:
        response = client.get(f"/api/incidents/{created['id']}/intelligence")
        assert response.status_code == 200, response.text
        body = response.json()
        wo_ids = {row["id"] for row in body["work_orders"]}
        assert wo_id in wo_ids
    finally:
        _delete_incident(created["id"])
        db = SessionLocal()
        try:
            from app.domain.repositories import WorkOrderRepository

            row = WorkOrderRepository(db).get(wo_id)
            if row:
                WorkOrderRepository(db).delete(row)
        finally:
            db.close()


def test_emergency_certified_technician_prioritization(client: TestClient) -> None:
    zone_id = _pick_zone(client)
    tech_id = f"TEC-INTEL-{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        TechnicianRepository(db).save(
            Technician(
                id=tech_id,
                name="ZZZ Emergency Last Alpha",
                role="Responder",
                primary_zone_id=zone_id,
                skills=["emergency"],
                utility_kinds=["water"],
                availability=TechnicianAvailability.AVAILABLE.value,
                emergency_certified=True,
            )
        )
    finally:
        db.close()

    created = _create_incident(
        client, scope="zone", zone_id=zone_id, utility_kind="water"
    )
    try:
        response = client.get(f"/api/incidents/{created['id']}/intelligence")
        assert response.status_code == 200
        techs = response.json()["technicians"]
        assert techs
        # First tech should prefer zone match + certified
        assert techs[0]["emergency_certified"] is True or any(
            t["id"] == tech_id for t in techs[:3]
        )
        assert any(t["id"] == tech_id for t in techs)
    finally:
        _delete_incident(created["id"])
        db = SessionLocal()
        try:
            tech = TechnicianRepository(db).get(tech_id)
            if tech:
                TechnicianRepository(db).delete(tech)
        finally:
            db.close()


def test_incident_not_found_intelligence(client: TestClient) -> None:
    response = client.get("/api/incidents/INC-DOES-NOT-EXIST/intelligence")
    assert response.status_code == 404


def test_hours_validation(client: TestClient) -> None:
    created = _create_incident(client, scope="corridor", utility_kind="water")
    try:
        bad = client.get(f"/api/incidents/{created['id']}/intelligence", params={"hours": 0})
        assert bad.status_code == 422
        bad2 = client.get(f"/api/incidents/{created['id']}/intelligence", params={"hours": 999})
        assert bad2.status_code == 422
    finally:
        _delete_incident(created["id"])


def test_api_response_schema_and_post_flow(client: TestClient) -> None:
    created = _create_incident(client, scope="corridor", utility_kind="water")
    try:
        response = client.get(f"/api/incidents/{created['id']}/intelligence", params={"hours": 24})
        assert response.status_code == 200, response.text
        body = response.json()
        for key in (
            "incident",
            "assessment_scope",
            "weather",
            "environment",
            "resilience",
            "assets",
            "work_orders",
            "technicians",
            "recommended_action",
            "inputs_available",
            "assessed_at",
        ):
            assert key in body
        assert body["incident"]["id"] == created["id"]
        assert body["recommended_action"]["summary"] is None
        assert "weather" in body["inputs_available"]

        # Lifecycle still works after intelligence
        ack = client.patch(
            f"/api/incidents/{created['id']}/status",
            json={"status": "acknowledged", "actor": "intel-test"},
        )
        assert ack.status_code == 200
        assert ack.json()["status"] == "acknowledged"
    finally:
        _delete_incident(created["id"])


def test_relevant_assets_for_zone(client: TestClient) -> None:
    zone_id = _pick_zone(client)
    created = _create_incident(
        client, scope="zone", zone_id=zone_id, utility_kind="water"
    )
    try:
        body = client.get(f"/api/incidents/{created['id']}/intelligence").json()
        for asset in body["assets"]:
            assert asset["zone"] == zone_id
            assert asset["utility"] == "water"
    finally:
        _delete_incident(created["id"])
