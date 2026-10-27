"""Phase 10B — GIS resilience context integration tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.schemas.environment_impact import (
    EnvironmentImpactAssessmentOut,
    EnvironmentImpactEvidenceOut,
)
from app.api.schemas.environment_signal import EnvironmentSignalScope
from app.api.schemas.resilience import (
    ResilienceAssessmentOut,
    ResilienceContextOut,
    ResilienceDriverOut,
    ResilienceInputsAvailabilityOut,
    ResilienceScope,
)
from app.api.schemas.weather import WeatherLocationOut, WeatherProviderMetaOut
from app.api.schemas.weather_impact import (
    WeatherImpactAssessmentOut,
    WeatherImpactScope,
    StaticAssetRiskMetadataOut,
)
from app.core.errors import WeatherUnavailableError
from app.db.session import SessionLocal
from app.domain.repositories.zone_repository import ZoneRepository
from app.integrations.environment.cache import reset_environment_caches_for_tests
from app.integrations.weather.cache import reset_weather_caches_for_tests
from app.main import app
from app.services.gis_service import GisService
from tests.test_environment_weather_api import RecordingWeatherProvider

NOW = datetime(2026, 9, 9, 17, 0, tzinfo=timezone.utc)


@pytest.fixture()
def db() -> Session:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    reset_weather_caches_for_tests()
    reset_environment_caches_for_tests()


def _location(**overrides: Any) -> WeatherLocationOut:
    base = {
        "latitude": 28.604417,
        "longitude": 77.21595,
        "timezone": "Asia/Kolkata",
        "scope": "corridor",
        "zone_id": None,
        "asset_count": 30,
        "utility_kind": "water",
        "method": "centroid_of_assets",
    }
    base.update(overrides)
    return WeatherLocationOut(**base)


def _meta(**overrides: Any) -> WeatherProviderMetaOut:
    base = {
        "provider": "test_weather",
        "fetched_at": NOW,
        "cache_status": "fresh",
        "cache_expires_at": None,
        "stale": False,
    }
    base.update(overrides)
    return WeatherProviderMetaOut(**base)


def _weather_assessment(**overrides: Any) -> WeatherImpactAssessmentOut:
    base: dict[str, Any] = {
        "location": _location(),
        "provider_meta": _meta(),
        "forecast_provider_meta": _meta(),
        "assessed_at": NOW,
        "scope": WeatherImpactScope.CORRIDOR,
        "zone_id": None,
        "forecast_hours": 6,
        "overall_severity": "high",
        "signals": [],
        "static_metadata_context": StaticAssetRiskMetadataOut(
            note="Static seed metadata only",
            utility_kind="water",
            zone_id=None,
            asset_count=30,
            weather_exposure={"low": 20, "medium": 10},
            flood_risk={"low": 15, "high": 5},
        ),
    }
    base.update(overrides)
    return WeatherImpactAssessmentOut(**base)


def _env_assessment(**overrides: Any) -> EnvironmentImpactAssessmentOut:
    base: dict[str, Any] = {
        "location": _location(),
        "provider_meta": _meta(provider="test_aq"),
        "assessed_at": NOW,
        "scope": EnvironmentSignalScope.CORRIDOR,
        "zone_id": None,
        "utility_kind": "water",
        "forecast_hours": 6,
        "observation_kinds": [],
        "overall_severity": "none",
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
    base: dict[str, Any] = {
        "scope": ResilienceScope.CORRIDOR,
        "zone_id": None,
        "utility_kind": "water",
        "overall_posture": "critical",
        "overall_severity": "critical",
        "drivers": [
            ResilienceDriverOut(
                source="weather",
                severity="high",
                explanation="Weather high",
                evidence={"overall_severity": "high"},
            ),
            ResilienceDriverOut(
                source="environment",
                severity="critical",
                explanation="Environment critical",
                evidence={"overall_severity": "critical", "aqi_standard": "us_aqi"},
            ),
            ResilienceDriverOut(
                source="scenario",
                severity="none",
                explanation="Scenario none",
                evidence={"scenario_key": "normal_operations"},
            ),
            ResilienceDriverOut(
                source="asset_posture",
                severity="medium",
                explanation="Static asset posture",
                evidence={
                    "asset_count": 30,
                    "high_criticality_no_backup": 2,
                    "static_metadata_only": True,
                },
            ),
        ],
        "evidence": ["Weather high", "Environment critical"],
        "assessed_at": NOW,
        "forecast_hours": 6,
        "inputs_available": ResilienceInputsAvailabilityOut(
            weather_available=True,
            environment_available=True,
            scenario_available=True,
            assets_available=True,
        ),
        "inputs_stale": False,
        "note": "POC operational posture",
    }
    base.update(overrides)
    return ResilienceAssessmentOut(**base)


class StubWeather:
    def __init__(self, assessments: dict[str | None, WeatherImpactAssessmentOut] | None = None) -> None:
        self._assessments = assessments or {None: _weather_assessment()}

    def assess(self, *, zone_id: str | None = None, hours: int = 48) -> WeatherImpactAssessmentOut:
        if zone_id in self._assessments:
            return self._assessments[zone_id]
        if None in self._assessments:
            return _weather_assessment(
                location=_location(scope="zone", zone_id=zone_id),
                scope=WeatherImpactScope.ZONE,
                zone_id=zone_id,
                overall_severity="none",
            )
        raise WeatherUnavailableError("missing")


class StubEnvironment:
    def assess(self, **kwargs: Any) -> EnvironmentImpactAssessmentOut:
        zone_id = kwargs.get("zone_id")
        if zone_id:
            return _env_assessment(
                location=_location(scope="zone", zone_id=zone_id),
                scope=EnvironmentSignalScope.ZONE,
                zone_id=zone_id,
            )
        return _env_assessment()


class StubResilience:
    def __init__(
        self,
        corridor: ResilienceAssessmentOut | None = None,
        zones: list[ResilienceAssessmentOut] | None = None,
        *,
        fail: bool = False,
        omit_zones: set[str] | None = None,
    ) -> None:
        self._corridor = corridor or _res_assessment()
        self._zones = zones
        self._fail = fail
        self._omit_zones = omit_zones or set()

    def get_context(self, *, utility_kind: str | None = None, hours: int = 24) -> ResilienceContextOut:
        if self._fail:
            raise RuntimeError("resilience down")
        if self._zones is not None:
            zones = [z for z in self._zones if z.zone_id not in self._omit_zones]
        else:
            # Caller GIS service will align to ZoneRepository; provide empty and let GIS mark missing
            zones = []
        return ResilienceContextOut(
            utility_kind=self._corridor.utility_kind,
            assessed_at=NOW,
            forecast_hours=hours,
            corridor=self._corridor,
            zones=zones,
            note="test resilience",
        )


def _gis(
    db: Session,
    *,
    resilience: StubResilience | None = None,
    weather: StubWeather | None = None,
) -> GisService:
    return GisService(
        db,
        impact_service=weather or StubWeather(),
        environment_impact_service=StubEnvironment(),
        resilience_assessment_service=resilience or StubResilience(),
    )


def test_gis_includes_corridor_resilience(db: Session) -> None:
    ctx = _gis(db).get_weather_context(forecast_hours=6)
    assert ctx.resilience is not None
    assert ctx.resilience.corridor is not None
    assert ctx.resilience.corridor_unavailable is False
    assert ctx.resilience.corridor.overall_posture == "critical"


def test_gis_includes_zone_resilience(db: Session) -> None:
    zones = [
        _res_assessment(
            scope=ResilienceScope.ZONE,
            zone_id="zone-c",
            overall_posture="high",
            overall_severity="high",
        )
    ]
    # Include all registered zones so none marked unavailable
    registered = list(ZoneRepository(db).list())
    zone_assessments = []
    for z in registered:
        if z.id == "zone-c":
            zone_assessments.append(zones[0])
        else:
            zone_assessments.append(
                _res_assessment(
                    scope=ResilienceScope.ZONE,
                    zone_id=z.id,
                    overall_posture="none",
                    overall_severity="none",
                )
            )
    ctx = _gis(db, resilience=StubResilience(zones=zone_assessments)).get_weather_context()
    assert ctx.resilience is not None
    zone_c = next(z for z in ctx.resilience.zones if z.zone_id == "zone-c")
    assert zone_c.assessment is not None
    assert zone_c.assessment.overall_posture == "high"
    assert zone_c.unavailable is False


def test_resilience_posture_preserved(db: Session) -> None:
    ctx = _gis(
        db,
        resilience=StubResilience(_res_assessment(overall_posture="high", overall_severity="high")),
    ).get_weather_context()
    assert ctx.resilience is not None
    assert ctx.resilience.corridor is not None
    assert ctx.resilience.corridor.overall_posture == "high"


def test_resilience_severity_preserved(db: Session) -> None:
    ctx = _gis(db).get_weather_context()
    assert ctx.resilience is not None
    assert ctx.resilience.corridor is not None
    assert ctx.resilience.corridor.overall_severity == "critical"


def test_resilience_drivers_preserved(db: Session) -> None:
    ctx = _gis(db).get_weather_context()
    assert ctx.resilience is not None
    assert ctx.resilience.corridor is not None
    sources = {str(d.source) for d in ctx.resilience.corridor.drivers}
    assert "weather" in sources
    assert "environment" in sources


def test_resilience_evidence_preserved(db: Session) -> None:
    ctx = _gis(db).get_weather_context()
    assert ctx.resilience is not None
    assert ctx.resilience.corridor is not None
    assert ctx.resilience.corridor.evidence
    env = next(d for d in ctx.resilience.corridor.drivers if str(d.source) == "environment")
    assert env.evidence.get("aqi_standard") == "us_aqi"


def test_utility_kind_preserved(db: Session) -> None:
    ctx = _gis(
        db,
        resilience=StubResilience(_res_assessment(utility_kind="water")),
    ).get_weather_context()
    assert ctx.utility_kind == "water"
    assert ctx.resilience is not None
    assert ctx.resilience.corridor is not None
    assert ctx.resilience.corridor.utility_kind == "water"


def test_unavailable_corridor_handling(db: Session) -> None:
    ctx = _gis(db, resilience=StubResilience(fail=True)).get_weather_context()
    assert ctx.resilience is not None
    assert ctx.resilience.corridor_unavailable is True
    assert ctx.resilience.corridor is None
    assert ctx.corridor.overall_severity == "high"  # weather intact


def test_unavailable_zone_handling(db: Session) -> None:
    # Provide corridor only — all zones missing → unavailable
    ctx = _gis(db, resilience=StubResilience(zones=[])).get_weather_context()
    assert ctx.resilience is not None
    assert ctx.resilience.zones
    assert all(z.unavailable and z.assessment is None for z in ctx.resilience.zones)


def test_no_fabricated_asset_resilience(db: Session) -> None:
    ctx = _gis(db).get_weather_context()
    assert ctx.resilience is not None
    note = ctx.resilience.spatial_note.lower()
    assert "corridor/zone" in note or "delhi" in note
    assert "0–100" in note or "0-100" in note or "score" in note
    blob = str(ctx.resilience.model_dump())
    assert "affected_asset_ids" not in blob


def test_static_asset_metadata_remains_separate(db: Session) -> None:
    ctx = _gis(db).get_weather_context()
    assert ctx.corridor.static_metadata_context is not None
    assert "static" in ctx.corridor.static_metadata_context.note.lower()
    assert ctx.corridor.static_metadata_context.weather_exposure
    assert ctx.corridor.static_metadata_context.flood_risk
    assert ctx.resilience is not None
    assert "not live flood" in ctx.resilience.spatial_note.lower()


def test_weather_gis_context_unchanged(db: Session) -> None:
    ctx = _gis(db).get_weather_context()
    assert ctx.corridor.overall_severity == "high"
    assert ctx.spatial_note
    assert "delhi" in ctx.spatial_note.lower()


def test_environment_gis_context_unchanged(db: Session) -> None:
    ctx = _gis(db).get_weather_context()
    assert ctx.environment is not None
    assert ctx.environment.corridor is not None
    assert ctx.environment.corridor_unavailable is False


def test_assets_unchanged(client: TestClient) -> None:
    response = client.get("/api/assets")
    assert response.status_code == 200
    assets = response.json()
    assert assets
    assert "aqi" not in assets[0]
    assert "overall_posture" not in assets[0]


def test_zones_unchanged(client: TestClient) -> None:
    response = client.get("/api/zones")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_benign_resilience_represented(db: Session) -> None:
    ctx = _gis(
        db,
        resilience=StubResilience(
            _res_assessment(overall_posture="none", overall_severity="none", drivers=[])
        ),
    ).get_weather_context()
    assert ctx.resilience is not None
    assert ctx.resilience.corridor is not None
    assert ctx.resilience.corridor.overall_posture == "none"


def test_stale_resilience_inputs_represented(db: Session) -> None:
    ctx = _gis(
        db,
        resilience=StubResilience(_res_assessment(inputs_stale=True)),
    ).get_weather_context()
    assert ctx.resilience is not None
    assert ctx.resilience.corridor is not None
    assert ctx.resilience.corridor.inputs_stale is True


def test_gis_api_integration(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    stub_res = StubResilience(
        _res_assessment(overall_posture="high", overall_severity="high"),
        zones=[],
    )

    def _factory(db, **kwargs):
        # Populate zone assessments from live zone registry for this request
        zone_rows = [
            _res_assessment(
                scope=ResilienceScope.ZONE,
                zone_id=z.id,
                overall_posture="medium",
                overall_severity="medium",
            )
            for z in ZoneRepository(db).list()
        ]
        return GisService(
            db,
            impact_service=StubWeather(),
            environment_impact_service=StubEnvironment(),
            resilience_assessment_service=StubResilience(
                _res_assessment(overall_posture="high", overall_severity="high"),
                zones=zone_rows,
            ),
        )

    monkeypatch.setattr("app.api.routers.gis.GisService", _factory)
    response = client.get("/api/gis/context", params={"hours": 6})
    assert response.status_code == 200
    body = response.json()
    assert "resilience" in body
    assert body["resilience"]["corridor"]["overall_posture"] == "high"
    assert body["corridor"]["overall_severity"] == "high"
    assert "environment" in body
    assert body["resilience"]["zones"]
    assert all(not z["unavailable"] for z in body["resilience"]["zones"])

def test_recommendation_api_regression(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.weather_service.build_weather_provider",
        lambda settings=None: RecordingWeatherProvider(),
    )
    monkeypatch.setattr(
        "app.services.operational_intelligence_service.OperationalIntelligenceService.weather_impact_rules",
        lambda self, impact_service=None, forecast_hours=48: [],
    )
    monkeypatch.setattr(
        "app.services.operational_intelligence_service.OperationalIntelligenceService.environment_impact_rules",
        lambda self, impact_service=None, forecast_hours=24: [],
    )
    monkeypatch.setattr(
        "app.services.operational_intelligence_service.OperationalIntelligenceService.resilience_rules",
        lambda self, resilience_service=None, forecast_hours=24: [],
    )
    response = client.get("/api/recommendations")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_resilience_api_regression(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock

    stub = StubResilience(_res_assessment(overall_posture="medium", overall_severity="medium"))
    monkeypatch.setattr(
        "app.api.routers.resilience.ResilienceAssessmentService",
        lambda db, **kwargs: MagicMock(get_context=lambda **kw: stub.get_context(**kw)),
    )
    response = client.get("/api/resilience")
    assert response.status_code == 200
    assert response.json()["corridor"]["overall_posture"] == "medium"


def test_resilience_spatial_note(db: Session) -> None:
    ctx = _gis(db).get_weather_context()
    assert ctx.resilience is not None
    assert "corridor/zone" in ctx.resilience.spatial_note.lower()
