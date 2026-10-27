"""Phase 8E — GIS weather context integration tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.schemas.weather import WeatherLocationOut, WeatherProviderMetaOut
from app.api.schemas.weather_impact import (
    WeatherImpactAssessmentOut,
    WeatherImpactObservationKind,
    WeatherImpactScope,
    WeatherImpactSignalOut,
    WeatherImpactSignalType,
)
from app.api.schemas.environment_impact import (
    EnvironmentImpactAssessmentOut,
    EnvironmentImpactEvidenceOut,
)
from app.api.schemas.environment_signal import EnvironmentSignalScope
from app.core.errors import WeatherUnavailableError
from app.db.session import SessionLocal
from app.domain.repositories.zone_repository import ZoneRepository
from app.integrations.environment.cache import reset_environment_caches_for_tests
from app.integrations.weather.cache import reset_weather_caches_for_tests
from app.integrations.weather.location_constants import (
    WEATHER_LOCATION_METHOD_CENTROID_OF_ASSETS,
)
from app.main import app
from app.services.gis_service import GisService
from tests.test_environment_weather_api import RecordingWeatherProvider

NOW = datetime(2026, 9, 2, 14, 0, tzinfo=timezone.utc)


class BenignEnvironmentImpact:
    """Deterministic no-signal environment stub so weather GIS tests stay offline."""

    def assess(self, *, zone_id: str | None = None, utility_kind: str | None = None, hours: int = 24):
        return EnvironmentImpactAssessmentOut(
            location=None,
            provider_meta=None,
            assessed_at=NOW,
            scope=EnvironmentSignalScope.ZONE if zone_id else EnvironmentSignalScope.CORRIDOR,
            zone_id=zone_id,
            utility_kind="water",
            forecast_hours=hours,
            observation_kinds=[],
            overall_severity="none",
            signals=[],
            evidence=EnvironmentImpactEvidenceOut(
                note="Test stub",
                threshold_basis="n/a",
                aqi_policy="us_aqi only",
            ),
            note=None,
        )


def _gis(db: Session, impact_service: Any) -> GisService:
    return GisService(
        db,
        impact_service=impact_service,
        environment_impact_service=BenignEnvironmentImpact(),
    )


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
        "method": WEATHER_LOCATION_METHOD_CENTROID_OF_ASSETS,
    }
    base.update(overrides)
    return WeatherLocationOut(**base)


def _provider_meta(**overrides: Any) -> WeatherProviderMetaOut:
    base = {
        "provider": "test_provider",
        "fetched_at": NOW,
        "cache_status": "fresh",
        "cache_expires_at": NOW + timedelta(minutes=10),
        "stale": False,
    }
    base.update(overrides)
    return WeatherProviderMetaOut(**base)


def _signal(**overrides: Any) -> WeatherImpactSignalOut:
    base = {
        "signal_type": WeatherImpactSignalType.PRECIPITATION,
        "severity": "high",
        "scope": WeatherImpactScope.CORRIDOR,
        "zone_id": None,
        "affected_asset_ids": [],
        "observation_kind": WeatherImpactObservationKind.CURRENT,
        "measured_value": 6.0,
        "unit": "mm",
        "threshold_rule": "precipitation_mm >= 5.0",
        "explanation": "Elevated rain.",
        "observed_at": NOW,
        "source": "test_provider",
    }
    base.update(overrides)
    return WeatherImpactSignalOut(**base)


def _assessment(signals: list[WeatherImpactSignalOut], **overrides: Any) -> WeatherImpactAssessmentOut:
    location = overrides.pop("location", _location())
    base = {
        "location": location,
        "provider_meta": _provider_meta(),
        "forecast_provider_meta": _provider_meta(),
        "assessed_at": NOW,
        "scope": WeatherImpactScope.CORRIDOR,
        "zone_id": None,
        "forecast_hours": 48,
        "overall_severity": "high" if signals else "none",
        "signals": signals,
        "static_metadata_context": None,
    }
    base.update(overrides)
    return WeatherImpactAssessmentOut(**base)


class StubImpactService:
    def __init__(self, assessments: dict[str | None, WeatherImpactAssessmentOut]) -> None:
        self._assessments = assessments

    def assess(self, *, zone_id: str | None = None, hours: int = 48) -> WeatherImpactAssessmentOut:
        if zone_id not in self._assessments:
            raise WeatherUnavailableError("missing")
        return self._assessments[zone_id]


def test_gis_weather_context_loads(db: Session) -> None:
    corridor = _assessment([])
    stub = StubImpactService({None: corridor, "zone-a": _assessment([], location=_location(scope="zone", zone_id="zone-a"))})
    ctx = _gis(db, stub).get_weather_context()
    assert ctx.corridor.overall_severity == "none"
    assert ctx.utility_kind == "water"
    assert len(ctx.zones) >= 1


def test_corridor_weather_context(db: Session) -> None:
    corridor = _assessment(
        [_signal(severity="critical", measured_value=12.0)],
        overall_severity="critical",
    )
    stub = StubImpactService({None: corridor})
    ctx = _gis(db, stub).get_weather_context()
    assert ctx.corridor.overall_severity == "critical"
    assert ctx.corridor.signals[0].affected_asset_ids == []


def test_zone_weather_context(db: Session) -> None:
    zone_assessment = _assessment(
        [_signal(scope=WeatherImpactScope.ZONE, zone_id="zone-c", severity="high")],
        location=_location(scope="zone", zone_id="zone-c"),
        scope=WeatherImpactScope.ZONE,
        zone_id="zone-c",
    )
    stub = StubImpactService({None: _assessment([]), "zone-c": zone_assessment})
    ctx = _gis(db, stub).get_weather_context()
    zone_c = next(z for z in ctx.zones if z.zone_id == "zone-c")
    assert zone_c.assessment is not None
    assert zone_c.assessment.overall_severity == "high"
    assert zone_c.unavailable is False


def test_benign_weather(db: Session) -> None:
    stub = StubImpactService({None: _assessment([])})
    ctx = _gis(db, stub).get_weather_context()
    assert ctx.corridor.signals == []


def test_adverse_weather(db: Session) -> None:
    stub = StubImpactService({None: _assessment([_signal()])})
    ctx = _gis(db, stub).get_weather_context()
    assert ctx.corridor.signals


def test_current_observation_in_signals(db: Session) -> None:
    stub = StubImpactService(
        {None: _assessment([_signal(observation_kind=WeatherImpactObservationKind.CURRENT)])}
    )
    ctx = _gis(db, stub).get_weather_context()
    assert ctx.corridor.signals[0].observation_kind == WeatherImpactObservationKind.CURRENT


def test_forecast_observation_in_signals(db: Session) -> None:
    stub = StubImpactService(
        {
            None: _assessment(
                [
                    _signal(
                        observation_kind=WeatherImpactObservationKind.FORECAST,
                        observed_at=NOW + timedelta(hours=3),
                    )
                ]
            )
        }
    )
    ctx = _gis(db, stub).get_weather_context()
    assert ctx.corridor.signals[0].observation_kind == WeatherImpactObservationKind.FORECAST


def test_provider_metadata_preserved(db: Session) -> None:
    meta = _provider_meta(provider="open_meteo", cache_status="cached")
    stub = StubImpactService({None: _assessment([], provider_meta=meta, forecast_provider_meta=meta)})
    ctx = _gis(db, stub).get_weather_context()
    assert ctx.corridor.provider_meta.provider == "open_meteo"
    assert ctx.corridor.provider_meta.cache_status == "cached"


def test_no_fabricated_affected_asset_ids(db: Session) -> None:
    stub = StubImpactService({None: _assessment([_signal()])})
    ctx = _gis(db, stub).get_weather_context()
    for signal in ctx.corridor.signals:
        assert signal.affected_asset_ids == []


def test_spatial_note_present(db: Session) -> None:
    stub = StubImpactService({None: _assessment([])})
    ctx = _gis(db, stub).get_weather_context()
    assert "delhi" in ctx.spatial_note.lower()
    assert "not" in ctx.spatial_note.lower()


def test_gis_context_api(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.weather_service.build_weather_provider",
        lambda settings=None: RecordingWeatherProvider(),
    )
    monkeypatch.setattr(
        "app.services.gis_service.EnvironmentImpactService.assess",
        lambda self, **kwargs: BenignEnvironmentImpact().assess(**kwargs),
    )
    response = client.get("/api/gis/context", params={"hours": 6})
    assert response.status_code == 200
    body = response.json()
    assert "corridor" in body
    assert "zones" in body
    assert "environment" in body
    assert body["utility_kind"] == "water"
    assert all(s["affected_asset_ids"] == [] for s in body["corridor"]["signals"])
    assert body["environment"]["corridor"]["overall_severity"] == "none"
    assert "resilience" in body
    assert body["resilience"]["corridor"] is not None
    assert body["resilience"]["corridor_unavailable"] is False


def test_gis_legacy_endpoint_still_works(client: TestClient) -> None:
    response = client.get("/api/gis")
    assert response.status_code == 200
    assert "gis/context" in response.json()["message"]


def test_assets_api_unchanged(client: TestClient) -> None:
    response = client.get("/api/assets")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_recommendations_still_work(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.weather_service.build_weather_provider",
        lambda settings=None: RecordingWeatherProvider(),
    )
    monkeypatch.setattr(
        "app.services.operational_intelligence_service.OperationalIntelligenceService.environment_impact_rules",
        lambda self, impact_service=None, forecast_hours=24: [],
    )
    monkeypatch.setattr(
        "app.services.operational_intelligence_service.OperationalIntelligenceService.weather_impact_rules",
        lambda self, impact_service=None, forecast_hours=48: [],
    )
    response = client.get("/api/recommendations")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_zone_mapping_matches_registered_zones(db: Session) -> None:
    stub = StubImpactService({None: _assessment([])})
    ctx = _gis(db, stub).get_weather_context()
    registered = {z.id for z in ZoneRepository(db).list()}
    returned = {z.zone_id for z in ctx.zones}
    assert registered == returned
    for row in ctx.zones:
        assert row.zone_name


def test_static_metadata_not_conflated_with_live_weather(db: Session) -> None:
    from app.api.schemas.weather_impact import StaticAssetRiskMetadataOut

    static_ctx = StaticAssetRiskMetadataOut(
        note="Static seed metadata only",
        utility_kind="water",
        zone_id=None,
        asset_count=30,
        weather_exposure={"zone-a": 4},
        flood_risk={"zone-a": 2},
    )
    stub = StubImpactService({None: _assessment([], static_metadata_context=static_ctx)})
    ctx = _gis(db, stub).get_weather_context()
    assert ctx.corridor.static_metadata_context is not None
    assert "static" in ctx.corridor.static_metadata_context.note.lower()
    assert ctx.corridor.static_metadata_context.weather_exposure


def test_zone_unavailable_when_impact_fails(db: Session) -> None:
    stub = StubImpactService({None: _assessment([])})
    ctx = _gis(db, stub).get_weather_context()
    assert any(z.unavailable and z.assessment is None for z in ctx.zones)


def test_gis_context_provider_unavailable(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.weather_service.build_weather_provider",
        lambda settings=None: RecordingWeatherProvider(fail=True),
    )
    monkeypatch.setattr(
        "app.services.gis_service.EnvironmentImpactService.assess",
        lambda self, **kwargs: BenignEnvironmentImpact().assess(**kwargs),
    )
    response = client.get("/api/gis/context")
    assert response.status_code == 503
    assert response.json()["code"] == "weather_unavailable"
