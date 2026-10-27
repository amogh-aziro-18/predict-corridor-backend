"""Phase 03 — Weather alert persistence from impact thresholds."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.api.schemas.weather import (
    CurrentWeatherOut,
    ForecastPointOut,
    WeatherForecastOut,
    WeatherLocationOut,
    WeatherProviderMetaOut,
)
from app.api.schemas.weather_impact import (
    StaticAssetRiskMetadataOut,
    WeatherImpactAssessmentOut,
    WeatherImpactScope,
    WeatherImpactSignalType,
)
from app.db.session import SessionLocal
from app.domain.enums import AlertSource, AlertStatus
from app.domain.repositories.alert_repository import AlertRepository
from app.integrations.weather.location_constants import (
    WEATHER_LOCATION_METHOD_CENTROID_OF_ASSETS,
)
from app.services.weather_alert_producer import (
    WEATHER_ALERT_UTILITY_KIND,
    known_weather_alert_codes,
    sync_weather_alerts_from_assessment,
    weather_alert_code,
)
from app.services.weather_impact_service import WeatherImpactService
from app.services.weather_impact_thresholds import (
    PRECIPITATION_ADVISORY_MM,
    PRECIPITATION_SEVERE_MM,
    classify_precipitation,
)

NOW = datetime(2026, 9, 2, 14, 0, tzinfo=timezone.utc)


@pytest.fixture()
def db() -> Session:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _cleanup_weather_alerts(db: Session) -> None:
    repo = AlertRepository(db)
    for alert in repo.list(source=AlertSource.WEATHER.value):
        repo.delete(alert)


@pytest.fixture(autouse=True)
def _clean_weather_alerts(db: Session) -> None:
    _cleanup_weather_alerts(db)
    yield
    _cleanup_weather_alerts(db)


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
        "cloud_cover_pct": 10.0,
    }
    base.update(overrides)
    return CurrentWeatherOut(**base)


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


def _forecast(hourly: list[ForecastPointOut] | None = None) -> WeatherForecastOut:
    return WeatherForecastOut(
        location=_location(),
        provider_meta=_provider_meta(),
        hourly=hourly or [_hourly()],
    )


class FakeWeatherService:
    def __init__(self, current: CurrentWeatherOut, forecast: WeatherForecastOut) -> None:
        self.current = current
        self.forecast = forecast

    def get_current(self, *, zone_id: str | None = None) -> CurrentWeatherOut:
        return self.current

    def get_forecast(self, *, zone_id: str | None = None, hours: int = 48) -> WeatherForecastOut:
        return self.forecast


def test_precipitation_threshold_boundaries() -> None:
    assert classify_precipitation(PRECIPITATION_ADVISORY_MM - 0.01) is None
    advisory = classify_precipitation(PRECIPITATION_ADVISORY_MM)
    assert advisory is not None
    assert advisory.severity == "medium"
    severe = classify_precipitation(PRECIPITATION_SEVERE_MM)
    assert severe is not None
    assert severe.severity == "critical"


def test_alert_codes_are_deterministic() -> None:
    code = weather_alert_code(signal_type="precipitation", observation_kind="current")
    assert code == "weather_precipitation_current"
    assert code in known_weather_alert_codes()


def test_impact_creates_weather_alert_and_dedupes(db: Session) -> None:
    fake = FakeWeatherService(
        _current(precipitation_mm=12.0, weather_code=65),
        _forecast([_hourly()]),
    )
    svc = WeatherImpactService(db, weather_service=fake)

    first = svc.assess()
    assert any(s.signal_type == WeatherImpactSignalType.PRECIPITATION for s in first.signals)

    alerts = AlertRepository(db).list(source=AlertSource.WEATHER.value, active_only=True)
    precip = [
        a
        for a in alerts
        if a.alert_code == "weather_precipitation_current"
        and a.utility_kind == WEATHER_ALERT_UTILITY_KIND
    ]
    assert len(precip) == 1
    assert precip[0].status == AlertStatus.OPEN.value
    assert precip[0].severity == "critical"
    fingerprint = precip[0].fingerprint
    alert_id = precip[0].id

    second = svc.assess()
    assert second.signals
    precip2 = [
        a
        for a in AlertRepository(db).list(source=AlertSource.WEATHER.value, active_only=True)
        if a.alert_code == "weather_precipitation_current"
    ]
    assert len(precip2) == 1
    assert precip2[0].fingerprint == fingerprint
    assert precip2[0].id == alert_id


def test_clearance_resolves_weather_alert(db: Session) -> None:
    fake = FakeWeatherService(
        _current(precipitation_mm=12.0),
        _forecast([_hourly()]),
    )
    svc = WeatherImpactService(db, weather_service=fake)
    svc.assess()
    assert any(
        a.alert_code == "weather_precipitation_current"
        for a in AlertRepository(db).list(source="weather", active_only=True)
    )

    fake.current = _current(precipitation_mm=0.0, weather_code=1)
    svc.assess()

    active = [
        a
        for a in AlertRepository(db).list(source="weather", active_only=True)
        if a.alert_code == "weather_precipitation_current"
    ]
    assert active == []

    resolved = [
        a
        for a in AlertRepository(db).list(source="weather")
        if a.alert_code == "weather_precipitation_current"
        and a.status == AlertStatus.RESOLVED.value
    ]
    assert len(resolved) >= 1
    assert resolved[0].resolve_notes == "Weather threshold condition cleared"


def test_no_alert_when_no_threshold_breach(db: Session) -> None:
    WeatherImpactService(
        db,
        weather_service=FakeWeatherService(
            _current(precipitation_mm=0.0, wind_speed_kmh=5.0, weather_code=1),
            _forecast([_hourly()]),
        ),
    ).assess()
    active = AlertRepository(db).list(source="weather", active_only=True)
    assert not any(a.alert_code == "weather_precipitation_current" for a in active)


def test_sync_empty_assessment_clears_scope(db: Session) -> None:
    WeatherImpactService(
        db,
        weather_service=FakeWeatherService(
            _current(precipitation_mm=12.0),
            _forecast([_hourly()]),
        ),
    ).assess()

    dry = WeatherImpactAssessmentOut(
        location=_location(),
        provider_meta=_provider_meta(),
        forecast_provider_meta=_provider_meta(),
        assessed_at=NOW,
        scope=WeatherImpactScope.CORRIDOR,
        zone_id=None,
        forecast_hours=48,
        overall_severity="none",
        signals=[],
        static_metadata_context=StaticAssetRiskMetadataOut(
            note="test",
            utility_kind="all_enabled",
            zone_id=None,
            asset_count=0,
            weather_exposure={},
            flood_risk={},
        ),
    )
    sync_weather_alerts_from_assessment(db, dry)
    assert not any(
        a.alert_code.startswith("weather_")
        for a in AlertRepository(db).list(source="weather", active_only=True)
    )
