"""Phase 8C pre-flight — weather impact contract and location metadata tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.api.schemas.weather import WeatherLocationOut, WeatherProviderMetaOut
from app.api.schemas.weather_impact import (
    WeatherImpactAssessmentOut,
    WeatherImpactObservationKind,
    WeatherImpactScope,
    WeatherImpactSignalOut,
    WeatherImpactSignalType,
)
from app.integrations.weather.location_constants import (
    WEATHER_LOCATION_METHOD_CENTROID_OF_ASSETS,
    WEATHER_LOCATION_METHOD_CONFIGURED_OPERATIONAL,
)
from app.integrations.weather.location import resolve_weather_location


def test_weather_impact_signal_contract_accepts_threshold_based_signal() -> None:
    signal = WeatherImpactSignalOut(
        signal_type=WeatherImpactSignalType.PRECIPITATION,
        severity="high",
        scope=WeatherImpactScope.CORRIDOR,
        zone_id=None,
        affected_asset_ids=[],
        observation_kind=WeatherImpactObservationKind.CURRENT,
        measured_value=12.4,
        unit="mm",
        threshold_rule="precipitation_mm >= 10.0",
        explanation="Precipitation 12.4 mm meets heavy-rain threshold for corridor scope.",
        observed_at=datetime(2026, 9, 2, 14, 0, tzinfo=timezone.utc),
        source="open_meteo",
    )
    assert signal.confidence is None
    assert signal.affected_asset_ids == []


def test_weather_impact_signal_rejects_invalid_confidence() -> None:
    with pytest.raises(ValidationError):
        WeatherImpactSignalOut(
            signal_type=WeatherImpactSignalType.WIND_SPEED,
            severity="medium",
            scope=WeatherImpactScope.ZONE,
            zone_id="zone-c",
            observation_kind=WeatherImpactObservationKind.FORECAST,
            measured_value=45.0,
            unit="km/h",
            threshold_rule="wind_speed_kmh >= 40.0",
            explanation="Forecast wind exceeds threshold.",
            observed_at=datetime(2026, 9, 2, 18, 0, tzinfo=timezone.utc),
            source="open_meteo",
            confidence=1.5,
        )


def test_weather_impact_assessment_contract_round_trip() -> None:
    now = datetime(2026, 9, 2, 14, 0, tzinfo=timezone.utc)
    assessment = WeatherImpactAssessmentOut(
        location=WeatherLocationOut(
            latitude=28.604417,
            longitude=77.21595,
            timezone="Asia/Kolkata",
            scope="corridor",
            zone_id=None,
            asset_count=30,
            utility_kind="water",
            method=WEATHER_LOCATION_METHOD_CENTROID_OF_ASSETS,
        ),
        provider_meta=WeatherProviderMetaOut(
            provider="open_meteo",
            fetched_at=now,
            cache_status="fresh",
            cache_expires_at=None,
            stale=False,
        ),
        forecast_provider_meta=WeatherProviderMetaOut(
            provider="open_meteo",
            fetched_at=now,
            cache_status="fresh",
            cache_expires_at=None,
            stale=False,
        ),
        assessed_at=now,
        scope=WeatherImpactScope.CORRIDOR,
        zone_id=None,
        forecast_hours=48,
        overall_severity="none",
        signals=[],
    )
    payload = assessment.model_dump()
    assert payload["scope"] == "corridor"
    assert payload["location"]["utility_kind"] == "water"
    assert payload["signals"] == []


def test_resolve_location_uses_configured_delhi_operational_point() -> None:
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        location = resolve_weather_location(db)
    finally:
        db.close()

    assert location.method == WEATHER_LOCATION_METHOD_CONFIGURED_OPERATIONAL
    assert location.utility_kind == "water"
    assert location.scope == "corridor"
    assert location.display_name == "Delhi, India"
    assert location.latitude == 28.6139
    assert location.longitude == 77.2090
    assert location.latitude > 20  # not Bangalore
