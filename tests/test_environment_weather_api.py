"""Phase 8A — external weather provider, cache, and API tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.integrations.weather.base import (
    NormalizedCurrentWeather,
    NormalizedForecastPoint,
    NormalizedWeatherForecast,
    WeatherLocation,
    WeatherProvider,
    WeatherProviderError,
)
from app.integrations.weather.cache import TtlCache, reset_weather_caches_for_tests
from app.integrations.weather.location import resolve_weather_location
from app.integrations.weather.open_meteo import OpenMeteoWeatherProvider
from app.main import app
from app.services.weather_service import WeatherService, build_weather_provider

SAMPLE_FORECAST_PAYLOAD: dict[str, Any] = {
    "latitude": 28.601234,
    "longitude": 77.214567,
    "timezone": "Asia/Kolkata",
    "current": {
        "time": "2026-09-02T14:00",
        "temperature_2m": 31.5,
        "relative_humidity_2m": 68.0,
        "precipitation": 0.2,
        "wind_speed_10m": 12.4,
        "wind_direction_10m": 225.0,
        "surface_pressure": 1004.3,
        "weather_code": 3,
    },
    "hourly": {
        "time": [
            "2026-09-02T14:00",
            "2026-09-02T15:00",
            "2026-09-02T16:00",
        ],
        "temperature_2m": [31.5, 32.0, 31.8],
        "relative_humidity_2m": [68.0, 65.0, 66.0],
        "precipitation": [0.2, 0.0, 0.4],
        "precipitation_probability": [10, 5, 20],
        "wind_speed_10m": [12.4, 13.0, 11.8],
        "wind_gusts_10m": [18.0, 19.2, 17.5],
        "surface_pressure": [1004.3, 1004.1, 1003.9],
        "weather_code": [3, 2, 3],
    },
}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    reset_weather_caches_for_tests()


class RecordingWeatherProvider(WeatherProvider):
    """In-memory provider for API/cache tests."""

    name = "recording_fake"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.current_calls = 0
        self.forecast_calls = 0

    def fetch_current(self, location: WeatherLocation) -> NormalizedCurrentWeather:
        self.current_calls += 1
        if self.fail:
            raise WeatherProviderError("simulated provider outage")
        now = datetime(2026, 9, 2, 14, 0, tzinfo=timezone.utc)
        return NormalizedCurrentWeather(
            observed_at=now,
            timezone="Asia/Kolkata",
            latitude=location.latitude,
            longitude=location.longitude,
            temperature_c=31.5,
            relative_humidity_pct=68.0,
            precipitation_mm=0.2,
            wind_speed_kmh=12.4,
            wind_direction_deg=225.0,
            pressure_hpa=1004.3,
            weather_code=3,
            provider=self.name,
            provider_fetched_at=now,
        )

    def fetch_forecast(
        self,
        location: WeatherLocation,
        *,
        hours: int = 48,
    ) -> NormalizedWeatherForecast:
        self.forecast_calls += 1
        if self.fail:
            raise WeatherProviderError("simulated provider outage")
        base = datetime(2026, 9, 2, 14, 0, tzinfo=timezone.utc)
        points = [
            NormalizedForecastPoint(
                timestamp=base + timedelta(hours=offset),
                temperature_c=31.5 + offset,
                relative_humidity_pct=68.0,
                precipitation_mm=0.1 * offset,
                precipitation_probability_pct=10.0,
                wind_speed_kmh=12.0,
                wind_gusts_kmh=18.0,
                pressure_hpa=1004.0,
                weather_code=3,
            )
            for offset in range(min(hours, 3))
        ]
        return NormalizedWeatherForecast(
            timezone="Asia/Kolkata",
            latitude=location.latitude,
            longitude=location.longitude,
            hourly=points,
            provider=self.name,
            provider_fetched_at=base,
        )


def test_open_meteo_normalizes_current_payload() -> None:
    provider = OpenMeteoWeatherProvider(client=httpx.Client())
    normalized = provider._normalize_current(SAMPLE_FORECAST_PAYLOAD)
    assert normalized.temperature_c == 31.5
    assert normalized.relative_humidity_pct == 68.0
    assert normalized.precipitation_mm == 0.2
    assert normalized.wind_speed_kmh == 12.4
    assert normalized.wind_direction_deg == 225.0
    assert normalized.pressure_hpa == 1004.3
    assert normalized.weather_code == 3
    assert normalized.provider == "open_meteo"
    assert normalized.timezone == "Asia/Kolkata"


def test_open_meteo_normalizes_forecast_payload() -> None:
    provider = OpenMeteoWeatherProvider(client=httpx.Client())
    normalized = provider._normalize_forecast(SAMPLE_FORECAST_PAYLOAD, hours=2)
    assert len(normalized.hourly) == 2
    assert normalized.hourly[0].temperature_c == 31.5
    assert normalized.hourly[1].temperature_c == 32.0
    assert normalized.hourly[0].precipitation_probability_pct == 10.0
    assert normalized.hourly[0].wind_gusts_kmh == 18.0


def test_open_meteo_adapter_uses_mock_transport() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "api.open-meteo.com" in str(request.url) or "forecast" in str(request.url)
        assert "latitude=" in str(request.url)
        assert "longitude=" in str(request.url)
        return httpx.Response(200, json=SAMPLE_FORECAST_PAYLOAD)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    provider = OpenMeteoWeatherProvider(
        base_url="https://api.open-meteo.com/v1/forecast",
        client=client,
    )
    location = WeatherLocation(
        latitude=28.601234,
        longitude=77.214567,
        scope="corridor",
        zone_id=None,
        asset_count=28,
        utility_kind="water",
        method="centroid_of_assets",
    )
    current = provider.fetch_current(location)
    assert current.temperature_c == 31.5
    forecast = provider.fetch_forecast(location, hours=3)
    assert len(forecast.hourly) == 3


def test_build_weather_provider_defaults_to_open_meteo() -> None:
    provider = build_weather_provider()
    assert provider.name == "open_meteo"


def test_resolve_location_from_water_assets() -> None:
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        location = resolve_weather_location(db)
    finally:
        db.close()

    assert location.method == "configured_operational_location"
    assert location.utility_kind == "water"
    assert location.scope == "corridor"
    assert location.display_name == "Delhi, India"
    assert location.city == "Delhi"
    assert location.country == "India"
    assert location.latitude == 28.6139
    assert location.longitude == 77.2090
    # Seeded Delhi NCR corridor band (must not be Bangalore ~12.97).
    assert 28.4 <= location.latitude <= 28.8
    assert 77.1 <= location.longitude <= 77.4
    assert location.latitude > 20  # not Bangalore


def test_zone_location_differs_from_corridor() -> None:
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        corridor = resolve_weather_location(db)
        zone_c = resolve_weather_location(db, zone_id="zone-c")
    finally:
        db.close()

    assert zone_c.scope == "zone"
    assert zone_c.zone_id == "zone-c"
    assert zone_c.asset_count >= 1
    # Zone anchors differ from corridor Delhi point.
    assert (
        corridor.latitude != zone_c.latitude
        or corridor.longitude != zone_c.longitude
    )


def test_current_weather_api_returns_normalized_payload(client: TestClient, monkeypatch) -> None:
    fake = RecordingWeatherProvider()
    monkeypatch.setattr(
        "app.services.weather_service.build_weather_provider",
        lambda settings=None: fake,
    )

    response = client.get("/api/environment/weather/current")
    assert response.status_code == 200
    body = response.json()
    assert body["provider_meta"]["provider"] == "recording_fake"
    assert body["provider_meta"]["stale"] is False
    assert body["temperature_c"] == 31.5
    assert body["location"]["method"] == "configured_operational_location"
    assert body["location"]["display_name"] == "Delhi, India"
    assert body["location"]["latitude"] == 28.6139
    assert body["location"]["longitude"] == 77.2090
    assert body["location"]["utility_kind"] == "water"
    assert body["location"]["asset_count"] >= 1
    assert fake.current_calls == 1


def test_forecast_weather_api_returns_hourly_points(client: TestClient, monkeypatch) -> None:
    fake = RecordingWeatherProvider()
    monkeypatch.setattr(
        "app.services.weather_service.build_weather_provider",
        lambda settings=None: fake,
    )

    response = client.get("/api/environment/weather/forecast", params={"hours": 3})
    assert response.status_code == 200
    body = response.json()
    assert len(body["hourly"]) == 3
    assert body["hourly"][0]["temperature_c"] == 31.5
    assert body["provider_meta"]["provider"] == "recording_fake"
    assert fake.forecast_calls == 1


def test_weather_router_aliases(client: TestClient, monkeypatch) -> None:
    fake = RecordingWeatherProvider()
    monkeypatch.setattr(
        "app.services.weather_service.build_weather_provider",
        lambda settings=None: fake,
    )

    current = client.get("/api/weather/current")
    assert current.status_code == 200
    forecast = client.get("/api/weather/forecast", params={"hours": 2})
    assert forecast.status_code == 200
    assert len(forecast.json()["hourly"]) == 2


def test_cache_reuses_fresh_current_result(client: TestClient, monkeypatch) -> None:
    fake = RecordingWeatherProvider()
    monkeypatch.setattr(
        "app.services.weather_service.build_weather_provider",
        lambda settings=None: fake,
    )

    first = client.get("/api/environment/weather/current")
    second = client.get("/api/environment/weather/current")
    assert first.status_code == 200
    assert second.status_code == 200
    assert fake.current_calls == 1
    assert second.json()["provider_meta"]["cache_status"] == "cached"


def test_stale_cache_returned_when_provider_fails(client: TestClient, monkeypatch) -> None:
    from app.integrations.weather.cache import get_current_weather_cache

    fake = RecordingWeatherProvider()
    monkeypatch.setattr(
        "app.services.weather_service.build_weather_provider",
        lambda settings=None: fake,
    )

    ok = client.get("/api/environment/weather/current")
    assert ok.status_code == 200

    # Expire cached entry so the next request attempts the provider.
    cache = get_current_weather_cache()
    cache.expire_all()

    fake.fail = True
    stale = client.get("/api/environment/weather/current")
    assert stale.status_code == 200
    meta = stale.json()["provider_meta"]
    assert meta["stale"] is True
    assert meta["cache_status"] == "stale"
    assert fake.current_calls == 2


def test_provider_failure_without_cache_returns_503(client: TestClient, monkeypatch) -> None:
    fake = RecordingWeatherProvider(fail=True)
    monkeypatch.setattr(
        "app.services.weather_service.build_weather_provider",
        lambda settings=None: fake,
    )

    response = client.get("/api/environment/weather/current")
    assert response.status_code == 503
    assert response.json()["code"] == "weather_unavailable"


def test_weather_service_cache_unit() -> None:
    from app.db.session import SessionLocal

    fake = RecordingWeatherProvider()
    current_cache = TtlCache()
    db = SessionLocal()
    try:
        service = WeatherService(
            db,
            provider=fake,
            current_cache=current_cache,
            forecast_cache=TtlCache(),
        )
        first = service.get_current()
        second = service.get_current()
    finally:
        db.close()

    assert first.provider_meta.cache_status == "fresh"
    assert second.provider_meta.cache_status == "cached"
    assert fake.current_calls == 1


def test_openapi_includes_weather_routes() -> None:
    paths = app.openapi()["paths"]
    assert "/api/environment/weather/current" in paths
    assert "/api/environment/weather/forecast" in paths
    assert "/api/weather/current" in paths
    assert "/api/weather/forecast" in paths
