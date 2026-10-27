"""Phase 9C — Environment Impact Assessment tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.schemas.environment_signal import (
    EnvironmentObservationKind,
    EnvironmentObservationSet,
    EnvironmentSignal,
    EnvironmentSignalCategory,
    EnvironmentSignalScope,
)
from app.api.schemas.weather import WeatherLocationOut, WeatherProviderMetaOut
from app.core.errors import EnvironmentUnavailableError
from app.db.session import SessionLocal
from app.integrations.environment.cache import reset_environment_caches_for_tests
from app.main import app
from app.services.environment_impact_service import EnvironmentImpactService
from app.services.environment_impact_thresholds import (
    classify_co,
    classify_no2,
    classify_o3,
    classify_pm10,
    classify_pm25,
    classify_so2,
    classify_us_aqi,
)
from app.services.environment_service import EnvironmentService
from tests.test_environment_weather_api import RecordingWeatherProvider

NOW = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)


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


def _signal(**overrides: Any) -> EnvironmentSignal:
    base: dict[str, Any] = {
        "category": EnvironmentSignalCategory.AIR_QUALITY,
        "signal_type": "aqi",
        "measured_value": 40.0,
        "unit": "US AQI",
        "observation_kind": EnvironmentObservationKind.CURRENT,
        "observed_at": NOW,
        "source": "open_meteo_air_quality",
        "scope": EnvironmentSignalScope.CORRIDOR,
        "zone_id": None,
        "utility_kind": "water",
        "location": _location(),
        "provider_meta": _meta(),
        "confidence": None,
        "metadata": {
            "aqi_standard": "us_aqi",
            "aqi_index": "United States Air Quality Index",
            "provider_field": "us_aqi",
        },
    }
    base.update(overrides)
    return EnvironmentSignal(**base)


def _observation_set(signals: list[EnvironmentSignal], **overrides: Any) -> EnvironmentObservationSet:
    base: dict[str, Any] = {
        "scope": EnvironmentSignalScope.CORRIDOR,
        "zone_id": None,
        "utility_kind": "water",
        "location": _location(),
        "provider_meta": _meta(),
        "observed_at": NOW,
        "generated_at": NOW,
        "signals": signals,
        "note": None,
    }
    base.update(overrides)
    return EnvironmentObservationSet(**base)


class StubEnvironmentService:
    def __init__(self, observation_set: EnvironmentObservationSet | None = None, *, fail: bool = False) -> None:
        self._set = observation_set
        self._fail = fail

    def get_observation_set(self, **kwargs: Any) -> EnvironmentObservationSet:
        if self._fail:
            raise EnvironmentUnavailableError("provider down")
        assert self._set is not None
        return self._set


def test_benign_aqi() -> None:
    assert classify_us_aqi(40.0) is None
    assessment = EnvironmentImpactService.__new__(EnvironmentImpactService).assess_observation_set(
        _observation_set([_signal(measured_value=40.0)]),
        forecast_hours=0,
    )
    assert assessment.overall_severity == "none"
    assert assessment.signals == []


def test_elevated_aqi_moderate_band() -> None:
    breach = classify_us_aqi(75.0)
    assert breach is not None
    assert breach.severity == "low"


def test_high_aqi_unhealthy_band() -> None:
    breach = classify_us_aqi(160.0)
    assert breach is not None
    assert breach.severity == "high"


def test_severe_aqi_hazardous_band() -> None:
    breach = classify_us_aqi(320.0)
    assert breach is not None
    assert breach.severity == "critical"


def test_us_aqi_metadata_preserved() -> None:
    svc = EnvironmentImpactService.__new__(EnvironmentImpactService)
    assessment = svc.assess_observation_set(
        _observation_set([_signal(measured_value=120.0)]),
        forecast_hours=0,
    )
    assert len(assessment.signals) == 1
    assert assessment.signals[0].metadata["aqi_standard"] == "us_aqi"
    assert "cpcb" not in assessment.signals[0].explanation.lower() or "not cpcb" in assessment.signals[0].explanation.lower()
    assert "Not CPCB" in assessment.signals[0].explanation or "not CPCB" in assessment.signals[0].explanation


def test_unknown_aqi_standard_not_classified() -> None:
    svc = EnvironmentImpactService.__new__(EnvironmentImpactService)
    assessment = svc.assess_observation_set(
        _observation_set(
            [
                _signal(
                    measured_value=200.0,
                    metadata={"aqi_standard": "cpcb", "provider_field": "cpcb_aqi"},
                )
            ]
        ),
        forecast_hours=0,
    )
    assert assessment.signals == []
    assert assessment.overall_severity == "none"


def test_missing_aqi_standard_not_classified() -> None:
    svc = EnvironmentImpactService.__new__(EnvironmentImpactService)
    assessment = svc.assess_observation_set(
        _observation_set([_signal(measured_value=200.0, metadata={})]),
        forecast_hours=0,
    )
    assert assessment.signals == []


def test_pm25_thresholds() -> None:
    assert classify_pm25(20.0) is None
    assert classify_pm25(40.0) is not None and classify_pm25(40.0).severity == "medium"
    assert classify_pm25(60.0) is not None and classify_pm25(60.0).severity == "high"
    assert classify_pm25(160.0) is not None and classify_pm25(160.0).severity == "critical"


def test_pm10_thresholds() -> None:
    assert classify_pm10(40.0) is None
    assert classify_pm10(60.0) is not None and classify_pm10(60.0).severity == "medium"
    assert classify_pm10(160.0) is not None and classify_pm10(160.0).severity == "high"


def test_no2_so2_co_o3_thresholds() -> None:
    assert classify_no2(50.0) is None
    assert classify_no2(150.0) is not None
    assert classify_so2(40.0) is None
    assert classify_so2(60.0) is not None
    assert classify_co(500.0) is None
    assert classify_co(1500.0) is not None
    assert classify_o3(80.0) is None
    assert classify_o3(120.0) is not None


def test_current_observation_kind() -> None:
    svc = EnvironmentImpactService.__new__(EnvironmentImpactService)
    assessment = svc.assess_observation_set(
        _observation_set([_signal(measured_value=120.0)]),
        forecast_hours=0,
    )
    assert assessment.signals[0].observation_kind == EnvironmentObservationKind.CURRENT
    assert EnvironmentObservationKind.CURRENT in assessment.observation_kinds


def test_forecast_observation_kind() -> None:
    svc = EnvironmentImpactService.__new__(EnvironmentImpactService)
    forecast = _signal(
        measured_value=180.0,
        observation_kind=EnvironmentObservationKind.FORECAST,
        observed_at=NOW + timedelta(hours=3),
    )
    assessment = svc.assess_observation_set(
        _observation_set([forecast]),
        forecast_hours=6,
    )
    assert assessment.signals[0].observation_kind == EnvironmentObservationKind.FORECAST
    assert "forecast" in assessment.signals[0].explanation.lower()
    assert assessment.note is not None
    assert "forward-looking" in assessment.note.lower()


def test_corridor_scope() -> None:
    svc = EnvironmentImpactService.__new__(EnvironmentImpactService)
    assessment = svc.assess_observation_set(
        _observation_set([_signal(measured_value=120.0)]),
        forecast_hours=0,
    )
    assert assessment.scope == EnvironmentSignalScope.CORRIDOR
    assert assessment.zone_id is None


def test_zone_scope_and_mapping() -> None:
    svc = EnvironmentImpactService.__new__(EnvironmentImpactService)
    zone_loc = _location(scope="zone", zone_id="zone-c", asset_count=7)
    signal = _signal(
        measured_value=120.0,
        scope=EnvironmentSignalScope.ZONE,
        zone_id="zone-c",
        location=zone_loc,
    )
    assessment = svc.assess_observation_set(
        _observation_set(
            [signal],
            scope=EnvironmentSignalScope.ZONE,
            zone_id="zone-c",
            location=zone_loc,
        ),
        forecast_hours=0,
    )
    assert assessment.scope == EnvironmentSignalScope.ZONE
    assert assessment.zone_id == "zone-c"
    assert assessment.signals[0].zone_id == "zone-c"


def test_worst_forecast_assessment() -> None:
    svc = EnvironmentImpactService.__new__(EnvironmentImpactService)
    mild = _signal(
        signal_type="pm25",
        measured_value=40.0,
        unit="μg/m³",
        observation_kind=EnvironmentObservationKind.FORECAST,
        observed_at=NOW + timedelta(hours=1),
        metadata={"provider_field": "pm2_5"},
    )
    severe = _signal(
        signal_type="pm25",
        measured_value=180.0,
        unit="μg/m³",
        observation_kind=EnvironmentObservationKind.FORECAST,
        observed_at=NOW + timedelta(hours=5),
        metadata={"provider_field": "pm2_5"},
    )
    assessment = svc.assess_observation_set(
        _observation_set([mild, severe]),
        forecast_hours=6,
    )
    pm25 = [s for s in assessment.signals if s.signal_type == "pm25"]
    assert len(pm25) == 1
    assert pm25[0].measured_value == 180.0
    assert pm25[0].severity == "critical"


def test_benign_combination_no_impact() -> None:
    svc = EnvironmentImpactService.__new__(EnvironmentImpactService)
    signals = [
        _signal(measured_value=30.0),
        _signal(signal_type="pm25", measured_value=10.0, unit="μg/m³", metadata={"provider_field": "pm2_5"}),
        _signal(signal_type="o3", measured_value=40.0, unit="μg/m³", metadata={"provider_field": "ozone"}),
    ]
    assessment = svc.assess_observation_set(_observation_set(signals), forecast_hours=0)
    assert assessment.overall_severity == "none"
    assert assessment.signals == []


def test_provider_unavailable(db: Session) -> None:
    stub = StubEnvironmentService(fail=True)
    service = EnvironmentImpactService(db, environment_service=stub)  # type: ignore[arg-type]
    with pytest.raises(EnvironmentUnavailableError):
        service.assess(hours=0)


def test_stale_provider_data_preserved() -> None:
    svc = EnvironmentImpactService.__new__(EnvironmentImpactService)
    stale_meta = _meta(cache_status="stale", stale=True)
    assessment = svc.assess_observation_set(
        _observation_set(
            [_signal(measured_value=120.0, provider_meta=stale_meta)],
            provider_meta=stale_meta,
            note="Returning stale cached air-quality observations after provider failure.",
        ),
        forecast_hours=0,
    )
    assert assessment.provider_meta is not None
    assert assessment.provider_meta.stale is True
    assert assessment.note is not None
    assert "stale" in assessment.note.lower()


def test_no_fabricated_affected_asset_ids() -> None:
    from app.api.schemas.environment_impact import EnvironmentImpactSignalOut

    assert "affected_asset_ids" not in EnvironmentImpactSignalOut.model_fields
    svc = EnvironmentImpactService.__new__(EnvironmentImpactService)
    assessment = svc.assess_observation_set(
        _observation_set([_signal(measured_value=160.0)]),
        forecast_hours=0,
    )
    payload = assessment.signals[0].model_dump()
    assert "affected_asset_ids" not in payload


def test_utility_neutrality() -> None:
    svc = EnvironmentImpactService.__new__(EnvironmentImpactService)
    assessment = svc.assess_observation_set(
        _observation_set(
            [_signal(measured_value=120.0, utility_kind="power")],
            utility_kind="power",
        ),
        forecast_hours=0,
    )
    assert assessment.utility_kind == "power"


def test_impact_api(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    observation = _observation_set([_signal(measured_value=160.0)])

    class _Stub:
        def get_observation_set(self, **kwargs: Any) -> EnvironmentObservationSet:
            return observation

    monkeypatch.setattr(
        "app.services.environment_impact_service.EnvironmentService",
        lambda db: _Stub(),
    )
    response = client.get("/api/environment/impact", params={"hours": 0})
    assert response.status_code == 200
    body = response.json()
    assert body["overall_severity"] in {"high", "critical", "medium", "low"}
    assert body["signals"]
    assert body["signals"][0]["metadata"]["aqi_standard"] == "us_aqi"
    assert "affected_asset_ids" not in body["signals"][0]


def test_environment_signals_still_works(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from app.integrations.environment.open_meteo_air_quality import OpenMeteoAirQualityProvider
    from tests.test_environment_air_quality_api import FakeTransport

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
    assert response.json()["signals"]


def test_weather_impact_still_works(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.weather_service.build_weather_provider",
        lambda settings=None: RecordingWeatherProvider(),
    )
    assert client.get("/api/environment/weather/impact", params={"hours": 6}).status_code == 200


def test_recommendations_and_gis_still_work(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.weather_service.build_weather_provider",
        lambda settings=None: RecordingWeatherProvider(),
    )
    assert client.get("/api/recommendations").status_code == 200
    assert client.get("/api/gis/context", params={"hours": 6}).status_code == 200


def test_evidence_rejects_cpcb_claim() -> None:
    svc = EnvironmentImpactService.__new__(EnvironmentImpactService)
    assessment = svc.assess_observation_set(
        _observation_set([_signal(measured_value=40.0)]),
        forecast_hours=0,
    )
    assert "CPCB" in assessment.evidence.threshold_basis or "cpcb" in assessment.evidence.aqi_policy.lower()
    assert "not" in assessment.evidence.threshold_basis.lower()
    assert "us_aqi" in assessment.evidence.aqi_policy
