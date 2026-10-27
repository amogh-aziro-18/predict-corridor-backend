"""Phase 9A — EnvironmentSignal contract and EnvironmentService foundation tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.schemas.environment_signal import (
    EnvironmentObservationKind,
    EnvironmentObservationSet,
    EnvironmentSignal,
    EnvironmentSignalCategory,
    EnvironmentSignalScope,
)
from app.api.schemas.weather import WeatherLocationOut, WeatherProviderMetaOut
from app.integrations.environment.base import EnvironmentProvider
from app.main import app
from app.services.environment_service import EnvironmentService

NOW = datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc)


def _location(**overrides: object) -> WeatherLocationOut:
    base: dict = {
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


def _provider_meta(**overrides: object) -> WeatherProviderMetaOut:
    base: dict = {
        "provider": "future_env_provider",
        "fetched_at": NOW,
        "cache_status": "fresh",
        "cache_expires_at": None,
        "stale": False,
    }
    base.update(overrides)
    return WeatherProviderMetaOut(**base)


def _signal(**overrides: object) -> EnvironmentSignal:
    base: dict = {
        "category": EnvironmentSignalCategory.WEATHER,
        "signal_type": "temperature",
        "measured_value": 31.5,
        "unit": "°C",
        "observation_kind": EnvironmentObservationKind.CURRENT,
        "observed_at": NOW,
        "source": "contract_test",
        "scope": EnvironmentSignalScope.CORRIDOR,
        "zone_id": None,
        "utility_kind": "water",
        "location": _location(),
        "provider_meta": None,
        "confidence": None,
        "metadata": {},
    }
    base.update(overrides)
    return EnvironmentSignal(**base)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_valid_corridor_signal() -> None:
    signal = _signal(scope=EnvironmentSignalScope.CORRIDOR, zone_id=None)
    assert signal.scope == EnvironmentSignalScope.CORRIDOR
    assert signal.zone_id is None
    assert signal.measured_value == 31.5


def test_valid_zone_signal() -> None:
    signal = _signal(
        scope=EnvironmentSignalScope.ZONE,
        zone_id="zone-c",
        location=_location(scope="zone", zone_id="zone-c", asset_count=7),
    )
    assert signal.scope == EnvironmentSignalScope.ZONE
    assert signal.zone_id == "zone-c"


def test_zone_requires_zone_id() -> None:
    with pytest.raises(ValidationError) as exc:
        _signal(scope=EnvironmentSignalScope.ZONE, zone_id=None)
    assert "zone_id" in str(exc.value)


def test_corridor_does_not_require_zone_id() -> None:
    signal = _signal(scope=EnvironmentSignalScope.CORRIDOR, zone_id=None)
    assert signal.zone_id is None


def test_current_observation() -> None:
    signal = _signal(observation_kind=EnvironmentObservationKind.CURRENT)
    assert signal.observation_kind == EnvironmentObservationKind.CURRENT


def test_forecast_observation() -> None:
    signal = _signal(observation_kind=EnvironmentObservationKind.FORECAST)
    assert signal.observation_kind == EnvironmentObservationKind.FORECAST


def test_historical_observation() -> None:
    signal = _signal(observation_kind=EnvironmentObservationKind.HISTORICAL)
    assert signal.observation_kind == EnvironmentObservationKind.HISTORICAL


def test_invalid_observation_kind() -> None:
    with pytest.raises(ValidationError):
        _signal(observation_kind="realtime")


def test_invalid_scope() -> None:
    with pytest.raises(ValidationError):
        _signal(scope="region")


def test_asset_scope_not_supported() -> None:
    with pytest.raises(ValidationError):
        _signal(scope="asset", zone_id=None)


def test_utility_kind_remains_generic() -> None:
    for kind in ("water", "power", "gas", "telecom"):
        signal = _signal(utility_kind=kind)
        assert signal.utility_kind == kind


def test_optional_provider_metadata() -> None:
    meta = _provider_meta(provider="open_meteo_air_quality")
    signal = _signal(provider_meta=meta)
    assert signal.provider_meta is not None
    assert signal.provider_meta.provider == "open_meteo_air_quality"


def test_optional_metadata() -> None:
    signal = _signal(metadata={"wmo_code": 65, "aqi_band": "moderate"})
    assert signal.metadata["wmo_code"] == 65
    assert signal.metadata["aqi_band"] == "moderate"


def test_optional_confidence() -> None:
    signal = _signal(confidence=0.85)
    assert signal.confidence == 0.85
    bare = _signal(confidence=None)
    assert bare.confidence is None


def test_environment_service_requires_db_for_live_fetch() -> None:
    """Without a DB session, live location resolution cannot run (no fabricated signals)."""
    from app.core.errors import EnvironmentUnavailableError

    service = EnvironmentService(db=None)
    with pytest.raises(EnvironmentUnavailableError):
        service.get_observation_set()


def test_environment_service_utility_kind_from_settings_not_hardcoded_in_provider() -> None:
    """utility_kind comes from caller/settings — provider receives location context only."""
    service = EnvironmentService(db=None)
    assert "water" not in (service._provider.name.lower())
    summary = service.summary()
    assert summary["provider_configured"] is True
    assert summary["phase"] == "9B"


def test_normalize_signal_round_trip() -> None:
    service = EnvironmentService(db=None)
    signal = _signal(
        category=EnvironmentSignalCategory.AIR_QUALITY,
        signal_type="pm25",
        measured_value=42.0,
        unit="µg/m³",
        utility_kind="gas",
    )
    normalized = service.normalize_signal(signal)
    assert normalized.signal_type == "pm25"
    assert normalized.utility_kind == "gas"


def test_observation_set_zone_requires_zone_id() -> None:
    with pytest.raises(ValidationError):
        EnvironmentObservationSet(
            scope=EnvironmentSignalScope.ZONE,
            zone_id=None,
            generated_at=NOW,
            signals=[],
        )


def test_environment_provider_is_abstract() -> None:
    assert getattr(EnvironmentProvider, "__abstractmethods__", None)
    with pytest.raises(TypeError):
        EnvironmentProvider()  # type: ignore[abstract]


def test_environment_signals_api_returns_real_or_unavailable(client: TestClient) -> None:
    """Live API must not return the Phase 9A empty foundation placeholder."""
    response = client.get("/api/environment/signals", params={"hours": 0})
    assert response.status_code in (200, 503)
    if response.status_code == 200:
        body = response.json()
        assert body["signals"], "expected real provider signals when available"
        assert body["location"] is not None
        assert body["provider_meta"] is not None
        assert all(s["category"] == "air_quality" for s in body["signals"])
    else:
        assert response.json()["code"] == "environment_unavailable"


def test_existing_weather_paths_unaffected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_environment_weather_api import RecordingWeatherProvider

    monkeypatch.setattr(
        "app.services.weather_service.build_weather_provider",
        lambda settings=None: RecordingWeatherProvider(),
    )
    for path in (
        "/api/environment/weather/current",
        "/api/environment/weather/forecast?hours=6",
        "/api/environment/weather/impact?hours=6",
    ):
        response = client.get(path)
        assert response.status_code == 200, path


def test_no_impact_fields_on_environment_signal() -> None:
    fields = EnvironmentSignal.model_fields
    for forbidden in ("severity", "threshold_rule", "explanation", "overall_severity", "affected_asset_ids"):
        assert forbidden not in fields
