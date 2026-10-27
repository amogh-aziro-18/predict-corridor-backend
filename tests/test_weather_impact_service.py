"""Phase 8C — Weather Impact Assessment service and API tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.api.schemas.weather import (
    CurrentWeatherOut,
    ForecastPointOut,
    WeatherForecastOut,
    WeatherLocationOut,
    WeatherProviderMetaOut,
)
from app.api.schemas.weather_impact import WeatherImpactObservationKind, WeatherImpactSignalType
from app.integrations.weather.cache import reset_weather_caches_for_tests
from app.integrations.weather.location_constants import (
    WEATHER_LOCATION_METHOD_CENTROID_OF_ASSETS,
)
from app.main import app
from app.services.weather_impact_service import WeatherImpactService
from app.services.weather_impact_thresholds import (
    classify_precipitation,
    classify_temperature,
    classify_weather_code,
    classify_wind_speed,
)
from app.services.weather_service import WeatherService
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


def _current(**overrides: Any) -> CurrentWeatherOut:
    base = {
        "location": _location(),
        "provider_meta": _provider_meta(),
        "observed_at": NOW,
        "temperature_c": 28.0,
        "relative_humidity_pct": 60.0,
        "precipitation_mm": 0.0,
        "wind_speed_kmh": 12.0,
        "wind_direction_deg": 180.0,
        "pressure_hpa": 1008.0,
        "weather_code": 1,
    }
    base.update(overrides)
    return CurrentWeatherOut(**base)


def _forecast(hourly: list[ForecastPointOut], **overrides: Any) -> WeatherForecastOut:
    base = {
        "location": _location(),
        "provider_meta": _provider_meta(),
        "hourly": hourly,
    }
    base.update(overrides)
    return WeatherForecastOut(**base)


def _hourly(**kwargs: Any) -> ForecastPointOut:
    base = {
        "timestamp": NOW + timedelta(hours=1),
        "temperature_c": 28.0,
        "relative_humidity_pct": 60.0,
        "precipitation_mm": 0.0,
        "precipitation_probability_pct": 10.0,
        "wind_speed_kmh": 12.0,
        "wind_gusts_kmh": 18.0,
        "pressure_hpa": 1008.0,
        "weather_code": 1,
    }
    base.update(kwargs)
    return ForecastPointOut(**base)


class FakeWeatherService:
    def __init__(self, current: CurrentWeatherOut, forecast: WeatherForecastOut) -> None:
        self._current = current
        self._forecast = forecast

    def get_current(self, *, zone_id: str | None = None) -> CurrentWeatherOut:
        return self._current

    def get_forecast(self, *, zone_id: str | None = None, hours: int = 48) -> WeatherForecastOut:
        return self._forecast


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    reset_weather_caches_for_tests()


def test_benign_weather_produces_no_adverse_signals(db: Session) -> None:
    service = WeatherImpactService(
        db,
        weather_service=FakeWeatherService(
            _current(),
            _forecast([_hourly()]),
        ),
    )
    assessment = service.assess()
    assert assessment.overall_severity == "none"
    assert assessment.signals == []
    assert all(s.affected_asset_ids == [] for s in assessment.signals)


def test_elevated_precipitation_current_signal(db: Session) -> None:
    service = WeatherImpactService(
        db,
        weather_service=FakeWeatherService(
            _current(precipitation_mm=6.0),
            _forecast([_hourly()]),
        ),
    )
    assessment = service.assess()
    precip = [s for s in assessment.signals if s.signal_type == WeatherImpactSignalType.PRECIPITATION]
    assert len(precip) == 1
    assert precip[0].severity == "high"
    assert precip[0].observation_kind == WeatherImpactObservationKind.CURRENT
    assert precip[0].affected_asset_ids == []


def test_severe_precipitation_current_signal(db: Session) -> None:
    service = WeatherImpactService(
        db,
        weather_service=FakeWeatherService(
            _current(precipitation_mm=12.0),
            _forecast([_hourly()]),
        ),
    )
    assessment = service.assess()
    precip = [s for s in assessment.signals if s.signal_type == WeatherImpactSignalType.PRECIPITATION][0]
    assert precip.severity == "critical"


def test_elevated_wind_current_signal(db: Session) -> None:
    service = WeatherImpactService(
        db,
        weather_service=FakeWeatherService(
            _current(wind_speed_kmh=55.0),
            _forecast([_hourly()]),
        ),
    )
    assessment = service.assess()
    wind = [s for s in assessment.signals if s.signal_type == WeatherImpactSignalType.WIND_SPEED][0]
    assert wind.severity == "high"
    assert wind.observation_kind == WeatherImpactObservationKind.CURRENT


def test_severe_wind_current_signal(db: Session) -> None:
    service = WeatherImpactService(
        db,
        weather_service=FakeWeatherService(
            _current(wind_speed_kmh=75.0),
            _forecast([_hourly()]),
        ),
    )
    assessment = service.assess()
    wind = [s for s in assessment.signals if s.signal_type == WeatherImpactSignalType.WIND_SPEED][0]
    assert wind.severity == "critical"


def test_temperature_threshold_signal(db: Session) -> None:
    service = WeatherImpactService(
        db,
        weather_service=FakeWeatherService(
            _current(temperature_c=41.0),
            _forecast([_hourly()]),
        ),
    )
    assessment = service.assess()
    temp = [s for s in assessment.signals if s.signal_type == WeatherImpactSignalType.TEMPERATURE][0]
    assert temp.severity == "high"


def test_weather_code_mapping_signal(db: Session) -> None:
    service = WeatherImpactService(
        db,
        weather_service=FakeWeatherService(
            _current(weather_code=95),
            _forecast([_hourly()]),
        ),
    )
    assessment = service.assess()
    code = [s for s in assessment.signals if s.signal_type == WeatherImpactSignalType.WEATHER_CODE][0]
    assert code.severity == "high"
    assert "Thunderstorm" in code.explanation


def test_forecast_precipitation_distinct_from_current(db: Session) -> None:
    service = WeatherImpactService(
        db,
        weather_service=FakeWeatherService(
            _current(precipitation_mm=0.0),
            _forecast([_hourly(precipitation_mm=11.0, timestamp=NOW + timedelta(hours=3))]),
        ),
    )
    assessment = service.assess()
    precip = [s for s in assessment.signals if s.signal_type == WeatherImpactSignalType.PRECIPITATION]
    assert len(precip) == 1
    assert precip[0].observation_kind == WeatherImpactObservationKind.FORECAST
    assert "forecast" in precip[0].explanation.lower()


def test_corridor_scope_assessment(db: Session) -> None:
    service = WeatherImpactService(
        db,
        weather_service=FakeWeatherService(_current(), _forecast([_hourly()])),
    )
    assessment = service.assess()
    assert assessment.scope == "corridor"
    assert assessment.zone_id is None
    assert assessment.location.scope == "corridor"


def test_zone_scope_assessment(db: Session) -> None:
    zone_location = _location(scope="zone", zone_id="zone-c", asset_count=7)
    service = WeatherImpactService(
        db,
        weather_service=FakeWeatherService(
            _current(location=zone_location),
            _forecast([_hourly()], location=zone_location),
        ),
    )
    assessment = service.assess(zone_id="zone-c")
    assert assessment.scope == "zone"
    assert assessment.zone_id == "zone-c"


def test_no_asset_level_weather_attribution(db: Session) -> None:
    service = WeatherImpactService(
        db,
        weather_service=FakeWeatherService(
            _current(precipitation_mm=12.0, wind_speed_kmh=80.0),
            _forecast([_hourly(precipitation_mm=15.0)]),
        ),
    )
    assessment = service.assess()
    assert assessment.signals
    assert all(s.affected_asset_ids == [] for s in assessment.signals)


def test_static_metadata_context_is_not_live_risk(db: Session) -> None:
    service = WeatherImpactService(
        db,
        weather_service=FakeWeatherService(_current(), _forecast([_hourly()])),
    )
    assessment = service.assess(zone_id="zone-c")
    ctx = assessment.static_metadata_context
    assert ctx is not None
    assert "Not live" in ctx.note or "not live" in ctx.note.lower()
    assert ctx.utility_kind == "all_enabled"
    assert ctx.zone_id == "zone-c"
    assert isinstance(ctx.weather_exposure, dict)
    assert isinstance(ctx.flood_risk, dict)
    # Multi-utility aggregation includes Communications seed assets in zone-c.
    assert ctx.asset_count >= 2
    assert not any("probability" in s.explanation.lower() for s in assessment.signals)


def test_provider_metadata_preserved(db: Session) -> None:
    current_meta = _provider_meta(cache_status="cached", stale=False)
    forecast_meta = _provider_meta(provider="forecast_provider", cache_status="fresh")
    service = WeatherImpactService(
        db,
        weather_service=FakeWeatherService(
            _current(provider_meta=current_meta),
            _forecast([_hourly()], provider_meta=forecast_meta),
        ),
    )
    assessment = service.assess()
    assert assessment.provider_meta.cache_status == "cached"
    assert assessment.forecast_provider_meta.provider == "forecast_provider"


def test_stale_provider_metadata_visible(db: Session) -> None:
    stale_meta = _provider_meta(cache_status="stale", stale=True)
    service = WeatherImpactService(
        db,
        weather_service=FakeWeatherService(
            _current(provider_meta=stale_meta),
            _forecast([_hourly()], provider_meta=stale_meta),
        ),
    )
    assessment = service.assess()
    assert assessment.provider_meta.stale is True
    assert assessment.provider_meta.cache_status == "stale"


def test_impact_api_corridor(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = RecordingWeatherProvider()
    monkeypatch.setattr(
        "app.services.weather_service.build_weather_provider",
        lambda settings=None: fake,
    )
    response = client.get("/api/environment/weather/impact", params={"hours": 6})
    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "corridor"
    assert body["forecast_hours"] == 6
    assert "provider_meta" in body
    assert "forecast_provider_meta" in body
    assert body["location"]["utility_kind"] == "water"
    assert all(s["affected_asset_ids"] == [] for s in body["signals"])


def test_impact_api_zone(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = RecordingWeatherProvider()
    monkeypatch.setattr(
        "app.services.weather_service.build_weather_provider",
        lambda settings=None: fake,
    )
    response = client.get("/api/environment/weather/impact", params={"zone": "zone-c", "hours": 6})
    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "zone"
    assert body["zone_id"] == "zone-c"


def test_provider_unavailable_returns_error(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = RecordingWeatherProvider(fail=True)
    monkeypatch.setattr(
        "app.services.weather_service.build_weather_provider",
        lambda settings=None: fake,
    )
    response = client.get("/api/environment/weather/impact")
    assert response.status_code == 503
    assert response.json()["code"] == "weather_unavailable"


def test_existing_weather_endpoints_still_work(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = RecordingWeatherProvider()
    monkeypatch.setattr(
        "app.services.weather_service.build_weather_provider",
        lambda settings=None: fake,
    )
    assert client.get("/api/environment/weather/current").status_code == 200
    assert client.get("/api/environment/weather/forecast", params={"hours": 6}).status_code == 200
    assert client.get("/api/weather/current").status_code == 200
    assert client.get("/api/weather/forecast", params={"hours": 6}).status_code == 200


def test_threshold_helpers() -> None:
    assert classify_precipitation(0.0) is None
    assert classify_precipitation(3.0).severity == "medium"
    assert classify_precipitation(6.0).severity == "high"
    assert classify_precipitation(12.0).severity == "critical"
    assert classify_wind_speed(25.0) is None
    assert classify_wind_speed(35.0).severity == "medium"
    assert classify_temperature(41.0).severity == "high"
    assert classify_weather_code(0) is None
    assert classify_weather_code(95).severity == "high"
