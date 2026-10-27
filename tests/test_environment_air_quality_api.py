"""Phase 9B — Open-Meteo Air Quality provider and EnvironmentService tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.errors import EnvironmentUnavailableError
from app.db.session import SessionLocal
from app.integrations.environment.base import (
    EnvironmentLocation,
    EnvironmentProviderError,
)
from app.integrations.environment.cache import reset_environment_caches_for_tests
from app.integrations.environment.open_meteo_air_quality import OpenMeteoAirQualityProvider
from app.integrations.weather.cache import TtlCache
from app.main import app
from app.services.environment_service import EnvironmentService
from tests.test_environment_weather_api import RecordingWeatherProvider

NOW = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)


def _sample_payload(*, hours: int = 6) -> dict[str, Any]:
    times = [(NOW + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(hours)]
    def series(base: float) -> list[float]:
        return [base + i * 0.1 for i in range(hours)]

    return {
        "latitude": 28.604,
        "longitude": 77.216,
        "timezone": "Asia/Kolkata",
        "current": {
            "time": NOW.strftime("%Y-%m-%dT%H:%M"),
            "us_aqi": 87,
            "pm2_5": 28.4,
            "pm10": 55.1,
            "carbon_monoxide": 210.0,
            "nitrogen_dioxide": 32.5,
            "sulphur_dioxide": 8.2,
            "ozone": 95.0,
        },
        "current_units": {
            "us_aqi": "USAQI",
            "pm2_5": "μg/m³",
            "pm10": "μg/m³",
            "carbon_monoxide": "μg/m³",
            "nitrogen_dioxide": "μg/m³",
            "sulphur_dioxide": "μg/m³",
            "ozone": "μg/m³",
        },
        "hourly": {
            "time": times,
            "us_aqi": series(90),
            "pm2_5": series(30),
            "pm10": series(60),
            "carbon_monoxide": series(200),
            "nitrogen_dioxide": series(35),
            "sulphur_dioxide": series(9),
            "ozone": series(100),
        },
        "hourly_units": {
            "us_aqi": "USAQI",
            "pm2_5": "μg/m³",
            "pm10": "μg/m³",
            "carbon_monoxide": "μg/m³",
            "nitrogen_dioxide": "μg/m³",
            "sulphur_dioxide": "μg/m³",
            "ozone": "μg/m³",
        },
    }


class FakeTransport(httpx.BaseTransport):
    def __init__(self, payload: dict[str, Any] | None = None, *, fail: bool = False) -> None:
        self.payload = payload if payload is not None else _sample_payload()
        self.fail = fail
        self.calls = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.fail:
            return httpx.Response(503, request=request, text="unavailable")
        return httpx.Response(200, json=self.payload, request=request)


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
    reset_environment_caches_for_tests()


def _location(**overrides: Any) -> EnvironmentLocation:
    base = {
        "latitude": 28.604417,
        "longitude": 77.21595,
        "scope": "corridor",
        "zone_id": None,
        "asset_count": 30,
        "utility_kind": "water",
        "method": "centroid_of_assets",
    }
    base.update(overrides)
    return EnvironmentLocation(**base)


def test_provider_normalization_current() -> None:
    transport = FakeTransport()
    client = httpx.Client(transport=transport)
    provider = OpenMeteoAirQualityProvider(client=client)
    obs = provider.fetch_observations(_location(), observation_kind="current", hours=0)
    assert obs
    assert all(o.observation_kind == "current" for o in obs)
    assert all(o.category == "air_quality" for o in obs)
    assert all(o.provider == "open_meteo_air_quality" for o in obs)


def test_aqi_normalization() -> None:
    provider = OpenMeteoAirQualityProvider(client=httpx.Client(transport=FakeTransport()))
    obs = provider.fetch_observations(_location(), observation_kind="current")
    aqi = next(o for o in obs if o.signal_type == "aqi")
    assert aqi.measured_value == 87.0
    assert aqi.metadata["aqi_standard"] == "us_aqi"
    assert aqi.unit == "US AQI"
    assert aqi.provider == "open_meteo_air_quality"


def test_aqi_explicitly_us_standard_not_cpcb() -> None:
    """Phase 9B.1 — AQI must be identified as US AQI, never CPCB/Indian National AQI."""
    provider = OpenMeteoAirQualityProvider(client=httpx.Client(transport=FakeTransport()))
    obs = provider.fetch_observations(_location(), observation_kind="current")
    aqi = next(o for o in obs if o.signal_type == "aqi")
    assert aqi.provider == "open_meteo_air_quality"
    assert aqi.metadata["aqi_standard"] == "us_aqi"
    assert aqi.metadata["provider_field"] == "us_aqi"
    assert "United States" in aqi.metadata["aqi_index"]
    semantics = str(aqi.metadata.get("aqi_semantics", "")).lower()
    assert "us aqi" in semantics or "us_aqi" in semantics
    assert "not cpcb" in semantics
    assert "indian" in semantics
    assert aqi.metadata["aqi_standard"] != "cpcb"
    assert aqi.metadata["aqi_standard"] != "indian_national"
    assert aqi.unit == "US AQI"
    assert aqi.metadata.get("provider_unit_label") == "USAQI"


def test_pollutant_signals_unaffected_by_aqi_semantics() -> None:
    provider = OpenMeteoAirQualityProvider(client=httpx.Client(transport=FakeTransport()))
    obs = provider.fetch_observations(_location(), observation_kind="current")
    pollutants = {o.signal_type: o for o in obs if o.signal_type != "aqi"}
    assert {"pm25", "pm10", "no2", "so2", "co", "o3"} <= set(pollutants)
    for signal in pollutants.values():
        assert "aqi_standard" not in signal.metadata
        assert "μg/m³" in signal.unit


def test_service_aqi_signal_preserves_us_standard(db: Session) -> None:
    provider = OpenMeteoAirQualityProvider(client=httpx.Client(transport=FakeTransport()))
    service = EnvironmentService(db, provider=provider, cache=TtlCache())
    result = service.get_observation_set(hours=0)
    aqi = next(s for s in result.signals if s.signal_type == "aqi")
    assert aqi.source == "open_meteo_air_quality"
    assert aqi.unit == "US AQI"
    assert aqi.metadata["aqi_standard"] == "us_aqi"
    assert "cpcb" not in aqi.unit.lower()
    assert "indian" not in aqi.unit.lower()


def test_pm25_normalization() -> None:
    provider = OpenMeteoAirQualityProvider(client=httpx.Client(transport=FakeTransport()))
    obs = provider.fetch_observations(_location(), observation_kind="current")
    pm25 = next(o for o in obs if o.signal_type == "pm25")
    assert pm25.measured_value == 28.4
    assert "μg/m³" in pm25.unit


def test_pm10_normalization() -> None:
    provider = OpenMeteoAirQualityProvider(client=httpx.Client(transport=FakeTransport()))
    obs = provider.fetch_observations(_location(), observation_kind="current")
    pm10 = next(o for o in obs if o.signal_type == "pm10")
    assert pm10.measured_value == 55.1


def test_no2_normalization() -> None:
    provider = OpenMeteoAirQualityProvider(client=httpx.Client(transport=FakeTransport()))
    obs = provider.fetch_observations(_location(), observation_kind="current")
    no2 = next(o for o in obs if o.signal_type == "no2")
    assert no2.measured_value == 32.5


def test_so2_normalization() -> None:
    provider = OpenMeteoAirQualityProvider(client=httpx.Client(transport=FakeTransport()))
    obs = provider.fetch_observations(_location(), observation_kind="current")
    so2 = next(o for o in obs if o.signal_type == "so2")
    assert so2.measured_value == 8.2


def test_co_normalization() -> None:
    provider = OpenMeteoAirQualityProvider(client=httpx.Client(transport=FakeTransport()))
    obs = provider.fetch_observations(_location(), observation_kind="current")
    co = next(o for o in obs if o.signal_type == "co")
    assert co.measured_value == 210.0


def test_o3_normalization() -> None:
    provider = OpenMeteoAirQualityProvider(client=httpx.Client(transport=FakeTransport()))
    obs = provider.fetch_observations(_location(), observation_kind="current")
    o3 = next(o for o in obs if o.signal_type == "o3")
    assert o3.measured_value == 95.0


def test_forecast_observation_kind() -> None:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    times = [(now + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(4)]
    payload = _sample_payload(hours=4)
    payload["hourly"]["time"] = times
    provider = OpenMeteoAirQualityProvider(client=httpx.Client(transport=FakeTransport(payload)))
    obs = provider.fetch_observations(_location(), observation_kind="forecast", hours=4)
    assert obs
    assert all(o.observation_kind == "forecast" for o in obs)


def test_corridor_and_zone_scope(db: Session) -> None:
    transport = FakeTransport()
    provider = OpenMeteoAirQualityProvider(client=httpx.Client(transport=transport))
    service = EnvironmentService(db, provider=provider, cache=TtlCache())
    corridor = service.get_observation_set(hours=0)
    assert corridor.scope.value == "corridor"
    assert corridor.zone_id is None
    assert corridor.signals
    zone = service.get_observation_set(zone_id="zone-c", hours=0)
    assert zone.scope.value == "zone"
    assert zone.zone_id == "zone-c"
    assert all(s.zone_id == "zone-c" for s in zone.signals)


def test_utility_kind_neutrality(db: Session) -> None:
    provider = OpenMeteoAirQualityProvider(client=httpx.Client(transport=FakeTransport()))
    service = EnvironmentService(db, provider=provider, cache=TtlCache())
    result = service.get_observation_set(utility_kind="water", hours=0)
    assert result.utility_kind == "water"
    assert all(s.utility_kind == "water" for s in result.signals)


def test_provider_metadata_present(db: Session) -> None:
    provider = OpenMeteoAirQualityProvider(client=httpx.Client(transport=FakeTransport()))
    service = EnvironmentService(db, provider=provider, cache=TtlCache())
    result = service.get_observation_set(hours=0)
    assert result.provider_meta is not None
    assert result.provider_meta.provider == "open_meteo_air_quality"
    assert result.provider_meta.cache_status == "fresh"
    assert result.signals[0].provider_meta is not None


def test_units_preserved() -> None:
    provider = OpenMeteoAirQualityProvider(client=httpx.Client(transport=FakeTransport()))
    obs = provider.fetch_observations(_location(), observation_kind="current")
    by_type = {o.signal_type: o for o in obs}
    assert "μg/m³" in by_type["pm25"].unit
    assert by_type["aqi"].unit == "US AQI"
    assert by_type["aqi"].metadata["aqi_standard"] == "us_aqi"


def test_provider_unavailable() -> None:
    provider = OpenMeteoAirQualityProvider(
        client=httpx.Client(transport=FakeTransport(fail=True))
    )
    with pytest.raises(EnvironmentProviderError):
        provider.fetch_observations(_location(), observation_kind="current")


def test_malformed_provider_response() -> None:
    transport = FakeTransport(payload={"latitude": 1.0, "longitude": 2.0})
    provider = OpenMeteoAirQualityProvider(client=httpx.Client(transport=transport))
    with pytest.raises(EnvironmentProviderError):
        provider.fetch_observations(_location(), observation_kind="current")


def test_cache_hit(db: Session) -> None:
    transport = FakeTransport()
    provider = OpenMeteoAirQualityProvider(client=httpx.Client(transport=transport))
    cache = TtlCache()
    service = EnvironmentService(db, provider=provider, cache=cache)
    first = service.get_observation_set(hours=0)
    second = service.get_observation_set(hours=0)
    assert transport.calls == 1
    assert first.provider_meta is not None and first.provider_meta.cache_status == "fresh"
    assert second.provider_meta is not None and second.provider_meta.cache_status == "cached"
    assert len(first.signals) == len(second.signals)


def test_cache_expiry_refetch(db: Session) -> None:
    transport = FakeTransport()
    provider = OpenMeteoAirQualityProvider(client=httpx.Client(transport=transport))
    cache = TtlCache()
    service = EnvironmentService(db, provider=provider, cache=cache)
    service.get_observation_set(hours=0)
    cache.expire_all()
    service.get_observation_set(hours=0)
    assert transport.calls == 2


def test_stale_cache_on_provider_failure(db: Session) -> None:
    transport = FakeTransport()
    provider = OpenMeteoAirQualityProvider(client=httpx.Client(transport=transport))
    cache = TtlCache()
    service = EnvironmentService(db, provider=provider, cache=cache)
    service.get_observation_set(hours=0)
    cache.expire_all()
    transport.fail = True
    stale = service.get_observation_set(hours=0)
    assert stale.provider_meta is not None
    assert stale.provider_meta.stale is True
    assert stale.provider_meta.cache_status == "stale"
    assert stale.signals


def test_no_fabricated_signals_on_total_failure(db: Session) -> None:
    transport = FakeTransport(fail=True)
    provider = OpenMeteoAirQualityProvider(client=httpx.Client(transport=transport))
    service = EnvironmentService(db, provider=provider, cache=TtlCache())
    with pytest.raises(EnvironmentUnavailableError):
        service.get_observation_set(hours=0)


def test_environment_service_integration(db: Session) -> None:
    provider = OpenMeteoAirQualityProvider(client=httpx.Client(transport=FakeTransport()))
    service = EnvironmentService(db, provider=provider, cache=TtlCache())
    result = service.get_observation_set(hours=0)
    types = {s.signal_type for s in result.signals}
    assert {"aqi", "pm25", "pm10", "no2", "so2", "co", "o3"} <= types
    assert all(s.category == "air_quality" or s.category.value == "air_quality" for s in result.signals)
    assert result.location is not None
    assert result.note is None


def test_environment_signals_api(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    transport = FakeTransport()

    def _factory(settings=None):
        return OpenMeteoAirQualityProvider(client=httpx.Client(transport=transport))

    monkeypatch.setattr(
        "app.services.environment_service.build_environment_provider",
        _factory,
    )
    reset_environment_caches_for_tests()
    response = client.get("/api/environment/signals", params={"hours": 0})
    assert response.status_code == 200
    body = response.json()
    assert body["signals"]
    assert body["provider_meta"]["provider"] == "open_meteo_air_quality"
    assert all(s["category"] == "air_quality" for s in body["signals"])
    assert all(s["source"] == "open_meteo_air_quality" for s in body["signals"])


def test_environment_signals_api_unavailable(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _factory(settings=None):
        return OpenMeteoAirQualityProvider(
            client=httpx.Client(transport=FakeTransport(fail=True))
        )

    monkeypatch.setattr(
        "app.services.environment_service.build_environment_provider",
        _factory,
    )
    reset_environment_caches_for_tests()
    response = client.get("/api/environment/signals", params={"hours": 0})
    assert response.status_code == 503
    assert response.json()["code"] == "environment_unavailable"


def test_existing_weather_endpoints_unaffected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.services.weather_service.build_weather_provider",
        lambda settings=None: RecordingWeatherProvider(),
    )
    for path in (
        "/api/environment/weather/current",
        "/api/environment/weather/forecast?hours=6",
        "/api/environment/weather/impact?hours=6",
    ):
        assert client.get(path).status_code == 200, path


def test_existing_gis_and_recommendations_unaffected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.services.weather_service.build_weather_provider",
        lambda settings=None: RecordingWeatherProvider(),
    )
    assert client.get("/api/gis/context", params={"hours": 6}).status_code == 200
    assert client.get("/api/recommendations").status_code == 200


def test_missing_optional_fields_skipped() -> None:
    payload = _sample_payload()
    del payload["current"]["ozone"]
    provider = OpenMeteoAirQualityProvider(client=httpx.Client(transport=FakeTransport(payload)))
    obs = provider.fetch_observations(_location(), observation_kind="current")
    assert all(o.signal_type != "o3" for o in obs)
    assert any(o.signal_type == "aqi" for o in obs)
