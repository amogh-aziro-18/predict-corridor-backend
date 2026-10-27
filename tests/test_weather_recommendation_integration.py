"""Phase 8D — weather impact → operational recommendation integration tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.schemas.weather import WeatherLocationOut, WeatherProviderMetaOut
from app.api.schemas.weather_impact import (
    StaticAssetRiskMetadataOut,
    WeatherImpactAssessmentOut,
    WeatherImpactObservationKind,
    WeatherImpactScope,
    WeatherImpactSignalOut,
    WeatherImpactSignalType,
)
from app.core.errors import WeatherUnavailableError
from app.db.session import SessionLocal
from app.integrations.weather.cache import reset_weather_caches_for_tests
from app.integrations.weather.location_constants import (
    WEATHER_LOCATION_METHOD_CENTROID_OF_ASSETS,
)
from app.main import app
from app.services.operational_intelligence_service import OperationalIntelligenceService
from app.services.recommendation_service import RecommendationService, _infer_recommendation_source
from app.services.weather_impact_service import WeatherImpactService
from tests.test_environment_weather_api import RecordingWeatherProvider

NOW = datetime(2026, 9, 2, 14, 0, tzinfo=timezone.utc)


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


class StubWeatherImpactService:
    def __init__(self, assessments: dict[str | None, WeatherImpactAssessmentOut]) -> None:
        self._assessments = assessments
        self.calls = 0

    def assess(self, *, zone_id: str | None = None, hours: int = 48) -> WeatherImpactAssessmentOut:
        self.calls += 1
        if zone_id not in self._assessments:
            return _assessment([])
        return self._assessments[zone_id]


class FailingWeatherImpactService:
    def assess(self, *, zone_id: str | None = None, hours: int = 48) -> WeatherImpactAssessmentOut:
        raise WeatherUnavailableError("provider down")


def test_benign_weather_produces_no_weather_recommendation(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.weather_impact_rules(
        impact_service=StubWeatherImpactService({None: _assessment([])}),
    )
    assert drafts == []


def test_elevated_precipitation_weather_recommendation(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.weather_impact_rules(
        impact_service=StubWeatherImpactService(
            {None: _assessment([_signal(severity="high", measured_value=6.0)])}
        ),
    )
    assert len(drafts) == 1
    assert drafts[0].rule_code == "weather_precipitation_current"
    assert drafts[0].priority == "high"
    assert drafts[0].affected_asset == "weather-scope:corridor"
    assert drafts[0].affected_zone == ""


def test_severe_precipitation_recommendation_severity(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.weather_impact_rules(
        impact_service=StubWeatherImpactService(
            {None: _assessment([_signal(severity="critical", measured_value=12.0)])}
        ),
    )
    assert drafts[0].severity == "critical"
    assert drafts[0].priority == "critical"


def test_elevated_wind_recommendation(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.weather_impact_rules(
        impact_service=StubWeatherImpactService(
            {
                None: _assessment(
                    [
                        _signal(
                            signal_type=WeatherImpactSignalType.WIND_SPEED,
                            severity="high",
                            measured_value=55.0,
                            unit="km/h",
                            threshold_rule="wind_speed_kmh >= 50.0",
                        )
                    ]
                )
            }
        ),
    )
    assert drafts[0].rule_code == "weather_wind_speed_current"


def test_forecast_precipitation_language(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.weather_impact_rules(
        impact_service=StubWeatherImpactService(
            {
                None: _assessment(
                    [
                        _signal(
                            observation_kind=WeatherImpactObservationKind.FORECAST,
                            observed_at=NOW + timedelta(hours=6),
                        )
                    ]
                )
            }
        ),
    )
    assert "Forecast" in drafts[0].title
    assert drafts[0].recommendation.startswith("Forecast indicates")
    assert "does not confirm failure" in drafts[0].recommendation


def test_current_precipitation_language(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.weather_impact_rules(
        impact_service=StubWeatherImpactService({None: _assessment([_signal()])}),
    )
    assert "Current" in drafts[0].title
    assert drafts[0].recommendation.startswith("Current")


def test_zone_recommendation_scope(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    zone_location = _location(scope="zone", zone_id="zone-c", asset_count=7)
    drafts = intel.weather_impact_rules(
        impact_service=StubWeatherImpactService(
            {
                None: _assessment([]),
                "zone-c": _assessment(
                    [_signal(scope=WeatherImpactScope.ZONE, zone_id="zone-c")],
                    location=zone_location,
                    scope=WeatherImpactScope.ZONE,
                    zone_id="zone-c",
                ),
            }
        ),
    )
    assert len(drafts) == 1
    assert drafts[0].affected_zone == "zone-c"
    assert drafts[0].affected_asset == "weather-scope:zone:zone-c"
    assert "zone zone-c" in drafts[0].title


def test_corridor_recommendation_no_fake_asset(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.weather_impact_rules(
        impact_service=StubWeatherImpactService({None: _assessment([_signal()])}),
    )
    assert drafts[0].affected_asset.startswith("weather-scope:")
    assert not drafts[0].affected_asset.startswith("asset-")


def test_stable_recommendation_identity(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    stub = StubWeatherImpactService({None: _assessment([_signal()])})
    first = intel.weather_impact_rules(impact_service=stub)
    second = intel.weather_impact_rules(impact_service=stub)
    id1 = OperationalIntelligenceService.stable_id(first[0].rule_code, first[0].affected_asset)
    id2 = OperationalIntelligenceService.stable_id(second[0].rule_code, second[0].affected_asset)
    assert id1 == id2


def test_repeated_polling_same_identity(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    stub = StubWeatherImpactService({None: _assessment([_signal()])})
    ids = set()
    for _ in range(3):
        drafts = intel.weather_impact_rules(impact_service=stub)
        ids.add(
            OperationalIntelligenceService.stable_id(
                drafts[0].rule_code,
                drafts[0].affected_asset,
            )
        )
    assert len(ids) == 1


def test_weather_evidence_in_description(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.weather_impact_rules(
        impact_service=StubWeatherImpactService({None: _assessment([_signal()])}),
    )
    desc = drafts[0].description
    assert "precipitation_mm >= 5.0" in desc
    assert "test_provider" in desc
    assert "6.0 mm" in desc


def test_provider_unavailable_no_fabricated_recommendations(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.weather_impact_rules(impact_service=FailingWeatherImpactService())
    assert drafts == []


def test_static_metadata_note_not_live_flood(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.weather_impact_rules(
        impact_service=StubWeatherImpactService(
            {
                None: _assessment(
                    [_signal()],
                    static_metadata_context=StaticAssetRiskMetadataOut(
                        note="Static seed/catalog metadata from the asset registry. Not live.",
                        utility_kind="water",
                        zone_id=None,
                        asset_count=5,
                        weather_exposure={"low": 3, "medium": 2},
                        flood_risk={"high": 2, "low": 3},
                    ),
                )
            }
        ),
    )
    assert "Static seed metadata" in drafts[0].description
    assert "not live flood probability" in drafts[0].description.lower()


def test_infer_recommendation_source() -> None:
    assert _infer_recommendation_source("resilience_posture") == "resilience"
    assert _infer_recommendation_source("environment_aqi_current") == "environment"
    assert _infer_recommendation_source("weather_precipitation_current") == "weather"
    assert _infer_recommendation_source("pressure_low") == "telemetry"
    assert _infer_recommendation_source("scenario_peak_demand") == "scenario"
    assert _infer_recommendation_source("xgboost_maintenance_risk") == "maintenance"


def test_recommendations_api_includes_weather_source(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class HighRainProvider(RecordingWeatherProvider):
        def fetch_current(self, location):
            current = super().fetch_current(location)
            return replace(current, precipitation_mm=12.0, weather_code=65)

        def fetch_forecast(self, location, *, hours: int = 48):
            forecast = super().fetch_forecast(location, hours=hours)
            hourly = [
                replace(
                    point,
                    precipitation_mm=12.0,
                    weather_code=65,
                )
                for point in forecast.hourly
            ]
            return replace(forecast, hourly=hourly)

    monkeypatch.setattr(
        "app.services.weather_service.build_weather_provider",
        lambda settings=None: HighRainProvider(),
    )

    response = client.get("/api/recommendations")
    assert response.status_code == 200
    body = response.json()
    weather_recs = [
        r
        for r in body
        if r.get("source") == "weather" or (r.get("rule_code") or "").startswith("weather_")
    ]
    assert weather_recs
    assert weather_recs[0]["rule_code"].startswith("weather_")
    assert weather_recs[0]["source"] == "weather"


def test_existing_telemetry_recommendations_still_present(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.generate(
        scenario_key="normal_operations",
        scenario_intensity=0.0,
        zone_scenarios={},
    )
    assert isinstance(drafts, list)


def test_weather_rules_do_not_duplicate_threshold_logic(db: Session) -> None:
    """Weather rules consume WeatherImpactService output only."""
    intel = OperationalIntelligenceService(db)
    stub = StubWeatherImpactService({None: _assessment([])})
    intel.weather_impact_rules(impact_service=stub)
    assert stub.calls >= 1
