"""Phase 9E — GIS environment context integration tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.schemas.environment_impact import (
    EnvironmentImpactAssessmentOut,
    EnvironmentImpactEvidenceOut,
    EnvironmentImpactSignalOut,
)
from app.api.schemas.environment_signal import EnvironmentObservationKind, EnvironmentSignalScope
from app.api.schemas.weather import WeatherLocationOut, WeatherProviderMetaOut
from app.api.schemas.weather_impact import (
    WeatherImpactAssessmentOut,
    WeatherImpactScope,
    StaticAssetRiskMetadataOut,
)
from app.core.errors import EnvironmentUnavailableError, WeatherUnavailableError
from app.db.session import SessionLocal
from app.domain.repositories.zone_repository import ZoneRepository
from app.integrations.environment.cache import reset_environment_caches_for_tests
from app.integrations.weather.cache import reset_weather_caches_for_tests
from app.main import app
from app.services.gis_service import GisService
from tests.test_environment_weather_api import RecordingWeatherProvider

NOW = datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc)


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
        "provider": "open_meteo_air_quality",
        "fetched_at": NOW,
        "cache_status": "fresh",
        "cache_expires_at": None,
        "stale": False,
    }
    base.update(overrides)
    return WeatherProviderMetaOut(**base)


def _evidence() -> EnvironmentImpactEvidenceOut:
    return EnvironmentImpactEvidenceOut(
        note="Deterministic threshold assessment.",
        threshold_basis="POC operational thresholds.",
        aqi_policy="Classify AQI only when metadata.aqi_standard == 'us_aqi'.",
    )


def _env_signal(**overrides: Any) -> EnvironmentImpactSignalOut:
    base: dict[str, Any] = {
        "signal_type": "aqi",
        "severity": "high",
        "scope": EnvironmentSignalScope.CORRIDOR,
        "zone_id": None,
        "observation_kind": EnvironmentObservationKind.CURRENT,
        "measured_value": 153.0,
        "unit": "US AQI",
        "threshold_rule": "us_aqi >= 150.0",
        "explanation": "US AQI elevated.",
        "observed_at": NOW,
        "source": "open_meteo_air_quality",
        "confidence": None,
        "metadata": {
            "aqi_standard": "us_aqi",
            "aqi_index": "United States Air Quality Index",
            "provider_field": "us_aqi",
        },
    }
    base.update(overrides)
    return EnvironmentImpactSignalOut(**base)


def _env_assessment(
    signals: list[EnvironmentImpactSignalOut],
    **overrides: Any,
) -> EnvironmentImpactAssessmentOut:
    base: dict[str, Any] = {
        "location": _location(),
        "provider_meta": _meta(),
        "assessed_at": NOW,
        "scope": EnvironmentSignalScope.CORRIDOR,
        "zone_id": None,
        "utility_kind": "water",
        "forecast_hours": 6,
        "observation_kinds": [EnvironmentObservationKind.CURRENT] if signals else [],
        "overall_severity": "high" if signals else "none",
        "signals": signals,
        "evidence": _evidence(),
        "note": None,
    }
    base.update(overrides)
    return EnvironmentImpactAssessmentOut(**base)


def _weather_assessment(**overrides: Any) -> WeatherImpactAssessmentOut:
    base = {
        "location": _location(),
        "provider_meta": WeatherProviderMetaOut(
            provider="test_weather",
            fetched_at=NOW,
            cache_status="fresh",
            cache_expires_at=None,
            stale=False,
        ),
        "forecast_provider_meta": WeatherProviderMetaOut(
            provider="test_weather",
            fetched_at=NOW,
            cache_status="fresh",
            cache_expires_at=None,
            stale=False,
        ),
        "assessed_at": NOW,
        "scope": WeatherImpactScope.CORRIDOR,
        "zone_id": None,
        "forecast_hours": 6,
        "overall_severity": "none",
        "signals": [],
        "static_metadata_context": StaticAssetRiskMetadataOut(
            note="Static seed metadata only",
            utility_kind="water",
            zone_id=None,
            asset_count=30,
            weather_exposure={"low": 23, "medium": 7},
            flood_risk={"low": 15, "medium": 8, "high": 7},
        ),
    }
    base.update(overrides)
    return WeatherImpactAssessmentOut(**base)


class StubWeatherImpact:
    def __init__(self, assessments: dict[str | None, WeatherImpactAssessmentOut] | None = None) -> None:
        self._assessments = assessments or {None: _weather_assessment()}

    def assess(self, *, zone_id: str | None = None, hours: int = 48) -> WeatherImpactAssessmentOut:
        if zone_id not in self._assessments:
            # Return benign zone weather when not explicitly stubbed
            if None in self._assessments:
                loc = _location(scope="zone", zone_id=zone_id)
                return _weather_assessment(
                    location=loc,
                    scope=WeatherImpactScope.ZONE,
                    zone_id=zone_id,
                )
            raise WeatherUnavailableError("missing weather")
        return self._assessments[zone_id]


class StubEnvironmentImpact:
    def __init__(
        self,
        assessments: dict[str | None, EnvironmentImpactAssessmentOut],
        *,
        fail_zones: set[str] | None = None,
        fail_corridor: bool = False,
    ) -> None:
        self._assessments = assessments
        self._fail_zones = fail_zones or set()
        self._fail_corridor = fail_corridor

    def assess(
        self,
        *,
        zone_id: str | None = None,
        utility_kind: str | None = None,
        hours: int = 24,
    ) -> EnvironmentImpactAssessmentOut:
        if zone_id is None and self._fail_corridor:
            raise EnvironmentUnavailableError("corridor env down")
        if zone_id is not None and zone_id in self._fail_zones:
            raise EnvironmentUnavailableError(f"zone {zone_id} env down")
        if zone_id not in self._assessments:
            return _env_assessment([])
        return self._assessments[zone_id]


def _gis(
    db: Session,
    *,
    env: StubEnvironmentImpact,
    weather: StubWeatherImpact | None = None,
) -> GisService:
    return GisService(
        db,
        impact_service=weather or StubWeatherImpact(),
        environment_impact_service=env,
    )


def test_gis_context_includes_corridor_environment(db: Session) -> None:
    env = StubEnvironmentImpact({None: _env_assessment([_env_signal()])})
    ctx = _gis(db, env=env).get_weather_context(forecast_hours=6)
    assert ctx.environment is not None
    assert ctx.environment.corridor is not None
    assert ctx.environment.corridor_unavailable is False
    assert ctx.environment.corridor.overall_severity == "high"


def test_gis_context_includes_zone_environment(db: Session) -> None:
    zone_loc = _location(scope="zone", zone_id="zone-c", asset_count=7)
    env = StubEnvironmentImpact(
        {
            None: _env_assessment([]),
            "zone-c": _env_assessment(
                [
                    _env_signal(
                        signal_type="pm25",
                        scope=EnvironmentSignalScope.ZONE,
                        zone_id="zone-c",
                        measured_value=66.0,
                        unit="μg/m³",
                        metadata={"provider_field": "pm2_5"},
                    )
                ],
                location=zone_loc,
                scope=EnvironmentSignalScope.ZONE,
                zone_id="zone-c",
                overall_severity="high",
            ),
        }
    )
    ctx = _gis(db, env=env).get_weather_context(forecast_hours=6)
    assert ctx.environment is not None
    zone_c = next(z for z in ctx.environment.zones if z.zone_id == "zone-c")
    assert zone_c.assessment is not None
    assert zone_c.assessment.signals[0].signal_type == "pm25"
    assert zone_c.unavailable is False


def test_environmental_severity_preserved(db: Session) -> None:
    env = StubEnvironmentImpact(
        {None: _env_assessment([_env_signal(severity="critical")], overall_severity="critical")}
    )
    ctx = _gis(db, env=env).get_weather_context()
    assert ctx.environment is not None
    assert ctx.environment.corridor is not None
    assert ctx.environment.corridor.overall_severity == "critical"
    assert ctx.environment.corridor.signals[0].severity == "critical"


def test_environmental_signal_values_preserved(db: Session) -> None:
    env = StubEnvironmentImpact(
        {None: _env_assessment([_env_signal(measured_value=153.0, unit="US AQI")])}
    )
    ctx = _gis(db, env=env).get_weather_context()
    assert ctx.environment is not None
    assert ctx.environment.corridor is not None
    signal = ctx.environment.corridor.signals[0]
    assert signal.measured_value == 153.0
    assert signal.unit == "US AQI"


def test_current_vs_forecast_preserved(db: Session) -> None:
    env = StubEnvironmentImpact(
        {
            None: _env_assessment(
                [
                    _env_signal(observation_kind=EnvironmentObservationKind.CURRENT),
                    _env_signal(
                        signal_type="pm10",
                        observation_kind=EnvironmentObservationKind.FORECAST,
                        measured_value=200.0,
                        unit="μg/m³",
                        metadata={"provider_field": "pm10"},
                    ),
                ],
                overall_severity="critical",
            )
        }
    )
    ctx = _gis(db, env=env).get_weather_context()
    assert ctx.environment is not None
    assert ctx.environment.corridor is not None
    kinds = {s.observation_kind for s in ctx.environment.corridor.signals}
    assert EnvironmentObservationKind.CURRENT in kinds
    assert EnvironmentObservationKind.FORECAST in kinds


def test_aqi_standard_preserved(db: Session) -> None:
    env = StubEnvironmentImpact({None: _env_assessment([_env_signal()])})
    ctx = _gis(db, env=env).get_weather_context()
    assert ctx.environment is not None
    assert ctx.environment.corridor is not None
    assert ctx.environment.corridor.signals[0].metadata["aqi_standard"] == "us_aqi"


def test_provider_metadata_preserved(db: Session) -> None:
    env = StubEnvironmentImpact(
        {
            None: _env_assessment(
                [_env_signal()],
                provider_meta=_meta(provider="open_meteo_air_quality", cache_status="cached"),
            )
        }
    )
    ctx = _gis(db, env=env).get_weather_context()
    assert ctx.environment is not None
    assert ctx.environment.corridor is not None
    assert ctx.environment.corridor.provider_meta is not None
    assert ctx.environment.corridor.provider_meta.provider == "open_meteo_air_quality"
    assert ctx.environment.corridor.provider_meta.cache_status == "cached"


def test_zone_unavailable_handled_honestly(db: Session) -> None:
    env = StubEnvironmentImpact(
        {None: _env_assessment([])},
        fail_zones={"zone-a"},
    )
    ctx = _gis(db, env=env).get_weather_context()
    assert ctx.environment is not None
    zone_a = next(z for z in ctx.environment.zones if z.zone_id == "zone-a")
    assert zone_a.unavailable is True
    assert zone_a.assessment is None
    # Weather path still present
    assert ctx.corridor is not None


def test_no_fabricated_asset_environmental_values(db: Session) -> None:
    env = StubEnvironmentImpact({None: _env_assessment([_env_signal()])})
    ctx = _gis(db, env=env).get_weather_context()
    assert ctx.environment is not None
    assert ctx.environment.corridor is not None
    for signal in ctx.environment.corridor.signals:
        dumped = signal.model_dump()
        assert "affected_asset_ids" not in dumped
    assert "not measured per asset" in ctx.environment.spatial_note.lower()

def test_static_weather_exposure_remains_static(db: Session) -> None:
    weather = StubWeatherImpact({None: _weather_assessment()})
    env = StubEnvironmentImpact({None: _env_assessment([_env_signal()])})
    ctx = _gis(db, env=env, weather=weather).get_weather_context()
    assert ctx.corridor.static_metadata_context is not None
    assert "static" in ctx.corridor.static_metadata_context.note.lower()
    assert ctx.corridor.static_metadata_context.weather_exposure
    # Environment block does not redefine static exposure
    assert ctx.environment is not None
    assert "weather_exposure" not in ctx.environment.model_dump()


def test_static_flood_risk_remains_static(db: Session) -> None:
    weather = StubWeatherImpact({None: _weather_assessment()})
    env = StubEnvironmentImpact({None: _env_assessment([])})
    ctx = _gis(db, env=env, weather=weather).get_weather_context()
    assert ctx.corridor.static_metadata_context is not None
    assert ctx.corridor.static_metadata_context.flood_risk
    assert ctx.environment is not None
    assert "seed metadata" in ctx.environment.spatial_note.lower()
    assert "not cpcb" in ctx.environment.spatial_note.lower() or "us aqi" in ctx.environment.spatial_note.lower()


def test_existing_weather_gis_context_intact(db: Session) -> None:
    weather = StubWeatherImpact({None: _weather_assessment(overall_severity="high")})
    env = StubEnvironmentImpact({None: _env_assessment([])})
    ctx = _gis(db, env=env, weather=weather).get_weather_context()
    assert ctx.corridor.overall_severity == "high"
    assert ctx.spatial_note
    assert "delhi" in ctx.spatial_note.lower()


def test_existing_asset_gis_context_intact(client: TestClient) -> None:
    response = client.get("/api/assets")
    assert response.status_code == 200
    assets = response.json()
    assert isinstance(assets, list)
    assert assets
    sample = assets[0]
    assert "id" in sample
    # No live AQI fields on assets
    assert "aqi" not in sample
    assert "pm25" not in sample


def test_utility_kind_preserved(db: Session) -> None:
    env = StubEnvironmentImpact(
        {None: _env_assessment([_env_signal()], utility_kind="water")}
    )
    ctx = _gis(db, env=env).get_weather_context()
    assert ctx.utility_kind == "water"
    assert ctx.environment is not None
    assert ctx.environment.corridor is not None
    assert ctx.environment.corridor.utility_kind == "water"


def test_benign_environmental_conditions(db: Session) -> None:
    env = StubEnvironmentImpact({None: _env_assessment([])})
    ctx = _gis(db, env=env).get_weather_context()
    assert ctx.environment is not None
    assert ctx.environment.corridor is not None
    assert ctx.environment.corridor.overall_severity == "none"
    assert ctx.environment.corridor.signals == []


def test_environment_provider_failure_handled(db: Session) -> None:
    env = StubEnvironmentImpact({None: _env_assessment([])}, fail_corridor=True)
    ctx = _gis(db, env=env).get_weather_context()
    assert ctx.environment is not None
    assert ctx.environment.corridor_unavailable is True
    assert ctx.environment.corridor is None
    # Weather still ok
    assert ctx.corridor.overall_severity == "none"


def test_gis_context_api_integration(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.weather_service.build_weather_provider",
        lambda settings=None: RecordingWeatherProvider(),
    )
    env = StubEnvironmentImpact({None: _env_assessment([_env_signal()])})
    weather = StubWeatherImpact()

    def _factory(db, **kwargs):
        return GisService(
            db,
            impact_service=weather,
            environment_impact_service=env,
        )

    monkeypatch.setattr("app.api.routers.gis.GisService", _factory)
    response = client.get("/api/gis/context", params={"hours": 6})
    assert response.status_code == 200
    body = response.json()
    assert "corridor" in body
    assert "environment" in body
    assert body["environment"]["corridor"]["overall_severity"] == "high"
    assert body["environment"]["corridor"]["signals"][0]["metadata"]["aqi_standard"] == "us_aqi"

def test_recommendations_regression(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.weather_service.build_weather_provider",
        lambda settings=None: RecordingWeatherProvider(),
    )
    # Keep environment recommendations from live path optional; stub env rules empty
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


def test_zone_list_matches_repository(db: Session) -> None:
    env = StubEnvironmentImpact({None: _env_assessment([])})
    ctx = _gis(db, env=env).get_weather_context()
    assert ctx.environment is not None
    registered = {z.id for z in ZoneRepository(db).list()}
    returned = {z.zone_id for z in ctx.environment.zones}
    assert registered == returned


def test_environment_spatial_note_honest(db: Session) -> None:
    env = StubEnvironmentImpact({None: _env_assessment([])})
    ctx = _gis(db, env=env).get_weather_context()
    assert ctx.environment is not None
    note = ctx.environment.spatial_note.lower()
    assert "delhi" in note
    assert "not measured per asset" in note
    assert "us aqi" in note or "cpcb" in note
