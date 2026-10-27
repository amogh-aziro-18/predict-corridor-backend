"""Phase 9D — environment impact → operational recommendation integration tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
from app.core.errors import EnvironmentUnavailableError
from app.db.session import SessionLocal
from app.integrations.environment.cache import reset_environment_caches_for_tests
from app.integrations.weather.cache import reset_weather_caches_for_tests
from app.main import app
from app.services.operational_intelligence_service import OperationalIntelligenceService
from app.services.recommendation_service import RecommendationService, _infer_recommendation_source

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
        threshold_basis="POC operational thresholds. Not CPCB.",
        aqi_policy="Classify AQI only when metadata.aqi_standard == 'us_aqi'.",
    )


def _signal(**overrides: Any) -> EnvironmentImpactSignalOut:
    base: dict[str, Any] = {
        "signal_type": "aqi",
        "severity": "medium",
        "scope": EnvironmentSignalScope.CORRIDOR,
        "zone_id": None,
        "observation_kind": EnvironmentObservationKind.CURRENT,
        "measured_value": 120.0,
        "unit": "US AQI",
        "threshold_rule": "us_aqi >= 100.0",
        "explanation": "US AQI 120 is unhealthy for sensitive groups. Not CPCB AQI.",
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


def _assessment(
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
        "forecast_hours": 24,
        "observation_kinds": [EnvironmentObservationKind.CURRENT] if signals else [],
        "overall_severity": "high" if signals else "none",
        "signals": signals,
        "evidence": _evidence(),
        "note": None,
    }
    base.update(overrides)
    return EnvironmentImpactAssessmentOut(**base)


class StubEnvironmentImpactService:
    def __init__(self, assessments: dict[str | None, EnvironmentImpactAssessmentOut]) -> None:
        self._assessments = assessments
        self.calls = 0

    def assess(
        self,
        *,
        zone_id: str | None = None,
        utility_kind: str | None = None,
        hours: int = 24,
    ) -> EnvironmentImpactAssessmentOut:
        self.calls += 1
        if zone_id not in self._assessments:
            return _assessment([])
        return self._assessments[zone_id]


class FailingEnvironmentImpactService:
    def assess(self, **kwargs: Any) -> EnvironmentImpactAssessmentOut:
        raise EnvironmentUnavailableError("provider down")


def test_benign_environment_no_recommendation(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.environment_impact_rules(
        impact_service=StubEnvironmentImpactService({None: _assessment([])}),
    )
    assert drafts == []


def test_elevated_aqi_recommendation(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.environment_impact_rules(
        impact_service=StubEnvironmentImpactService(
            {None: _assessment([_signal(severity="low", measured_value=75.0)])}
        ),
    )
    assert len(drafts) == 1
    assert drafts[0].rule_code == "environment_aqi_current"
    assert drafts[0].priority == "low"
    assert "us_aqi" in drafts[0].description
    assert "Not CPCB" in drafts[0].description or "not CPCB" in drafts[0].description


def test_high_aqi_recommendation(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.environment_impact_rules(
        impact_service=StubEnvironmentImpactService(
            {None: _assessment([_signal(severity="high", measured_value=160.0)])}
        ),
    )
    assert drafts[0].priority == "high"
    assert drafts[0].severity == "high"


def test_severe_aqi_recommendation(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.environment_impact_rules(
        impact_service=StubEnvironmentImpactService(
            {None: _assessment([_signal(severity="critical", measured_value=320.0)])}
        ),
    )
    assert drafts[0].priority == "critical"


def test_pm25_recommendation(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.environment_impact_rules(
        impact_service=StubEnvironmentImpactService(
            {
                None: _assessment(
                    [
                        _signal(
                            signal_type="pm25",
                            severity="high",
                            measured_value=60.0,
                            unit="μg/m³",
                            threshold_rule="pm25_ug_m3 >= 55.0",
                            explanation="PM2.5 elevated POC threshold.",
                            metadata={"provider_field": "pm2_5"},
                        )
                    ]
                )
            }
        ),
    )
    assert drafts[0].rule_code == "environment_pm25_current"
    assert "pm25" in drafts[0].description.lower() or "pm2" in drafts[0].title.lower()


def test_pm10_recommendation(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.environment_impact_rules(
        impact_service=StubEnvironmentImpactService(
            {
                None: _assessment(
                    [
                        _signal(
                            signal_type="pm10",
                            severity="medium",
                            measured_value=70.0,
                            unit="μg/m³",
                            threshold_rule="pm10_ug_m3 >= 55.0",
                            explanation="PM10 advisory.",
                            metadata={"provider_field": "pm10"},
                        )
                    ]
                )
            }
        ),
    )
    assert drafts[0].rule_code == "environment_pm10_current"


def test_no2_so2_co_o3_recommendations(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    for signal_type, value, unit in (
        ("no2", 150.0, "μg/m³"),
        ("so2", 60.0, "μg/m³"),
        ("co", 1500.0, "μg/m³"),
        ("o3", 120.0, "μg/m³"),
    ):
        drafts = intel.environment_impact_rules(
            impact_service=StubEnvironmentImpactService(
                {
                    None: _assessment(
                        [
                            _signal(
                                signal_type=signal_type,
                                severity="medium",
                                measured_value=value,
                                unit=unit,
                                threshold_rule=f"{signal_type}_ug_m3 >= 1",
                                explanation=f"{signal_type} advisory.",
                                metadata={"provider_field": signal_type},
                            )
                        ]
                    )
                }
            ),
        )
        assert drafts[0].rule_code == f"environment_{signal_type}_current"


def test_current_recommendation_wording(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.environment_impact_rules(
        impact_service=StubEnvironmentImpactService({None: _assessment([_signal()])}),
    )
    assert drafts[0].title.startswith("Current environmental")
    assert drafts[0].recommendation.startswith("Current environmental condition")
    assert "observation_kind=current" in drafts[0].description


def test_forecast_recommendation_wording(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.environment_impact_rules(
        impact_service=StubEnvironmentImpactService(
            {
                None: _assessment(
                    [
                        _signal(
                            observation_kind=EnvironmentObservationKind.FORECAST,
                            observed_at=NOW + timedelta(hours=4),
                        )
                    ]
                )
            }
        ),
    )
    assert drafts[0].title.startswith("Forecast environmental")
    assert drafts[0].recommendation.startswith("Forecast environmental condition")
    assert drafts[0].rule_code == "environment_aqi_forecast"
    assert "observation_kind=forecast" in drafts[0].description


def test_us_aqi_metadata_preserved(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.environment_impact_rules(
        impact_service=StubEnvironmentImpactService({None: _assessment([_signal()])}),
    )
    assert "us_aqi" in drafts[0].description
    assert "United States" in drafts[0].description or "US AQI" in drafts[0].recommendation
    assert "CPCB" in drafts[0].description or "CPCB" in drafts[0].recommendation


def test_unknown_aqi_standard_no_recommendation(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.environment_impact_rules(
        impact_service=StubEnvironmentImpactService(
            {
                None: _assessment(
                    [
                        _signal(
                            measured_value=200.0,
                            metadata={"aqi_standard": "cpcb"},
                        )
                    ]
                )
            }
        ),
    )
    assert drafts == []


def test_corridor_scope(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.environment_impact_rules(
        impact_service=StubEnvironmentImpactService({None: _assessment([_signal()])}),
    )
    assert drafts[0].affected_asset == "environment-scope:corridor"
    assert drafts[0].affected_zone == ""


def test_zone_scope(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    zone_loc = _location(scope="zone", zone_id="zone-c", asset_count=7)
    drafts = intel.environment_impact_rules(
        impact_service=StubEnvironmentImpactService(
            {
                None: _assessment([]),
                "zone-c": _assessment(
                    [
                        _signal(
                            scope=EnvironmentSignalScope.ZONE,
                            zone_id="zone-c",
                        )
                    ],
                    location=zone_loc,
                    scope=EnvironmentSignalScope.ZONE,
                    zone_id="zone-c",
                ),
            }
        ),
    )
    assert len(drafts) == 1
    assert drafts[0].affected_asset == "environment-scope:zone:zone-c"
    assert drafts[0].affected_zone == "zone-c"


def test_stable_identity_across_polls(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    stub = StubEnvironmentImpactService({None: _assessment([_signal()])})
    ids = {
        OperationalIntelligenceService.stable_id(d.rule_code, d.affected_asset)
        for _ in range(3)
        for d in intel.environment_impact_rules(impact_service=stub)
    }
    assert len(ids) == 1


def test_no_timestamp_in_identity(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.environment_impact_rules(
        impact_service=StubEnvironmentImpactService({None: _assessment([_signal()])}),
    )
    assert "2026" not in drafts[0].rule_code
    assert "T14" not in drafts[0].affected_asset


def test_no_fabricated_asset_id(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.environment_impact_rules(
        impact_service=StubEnvironmentImpactService({None: _assessment([_signal()])}),
    )
    assert drafts[0].affected_asset.startswith("environment-scope:")
    assert not drafts[0].affected_asset.startswith("asset-")


def test_provider_unavailable_no_environment_recommendation(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.environment_impact_rules(impact_service=FailingEnvironmentImpactService())
    assert drafts == []


def test_stale_data_represented(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.environment_impact_rules(
        impact_service=StubEnvironmentImpactService(
            {
                None: _assessment(
                    [_signal()],
                    provider_meta=_meta(stale=True, cache_status="stale"),
                    note="Returning stale cached air-quality observations after provider failure.",
                )
            }
        ),
    )
    assert "stale" in drafts[0].description.lower()


def test_utility_neutrality(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.environment_impact_rules(
        impact_service=StubEnvironmentImpactService(
            {None: _assessment([_signal()], utility_kind="power")}
        ),
    )
    assert drafts
    # Scope key remains environment-scope; utility comes from assessment context
    assert drafts[0].affected_asset.startswith("environment-scope:")


def test_infer_environment_source() -> None:
    assert _infer_recommendation_source("environment_aqi_current") == "environment"
    assert _infer_recommendation_source("weather_precipitation_current") == "weather"
    assert _infer_recommendation_source("xgboost_maintenance_risk") == "maintenance"
    assert _infer_recommendation_source("scenario_peak_demand") == "scenario"
    assert _infer_recommendation_source("pressure_low") == "telemetry"


def test_existing_telemetry_recommendations_still_work(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.generate(
        scenario_key="normal_operations",
        scenario_intensity=0.0,
        zone_scenarios={},
    )
    assert isinstance(drafts, list)


def test_existing_scenario_recommendations_still_work(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.generate(
        scenario_key="pump_failure",
        scenario_intensity=0.8,
        zone_scenarios={},
    )
    assert any((d.rule_code or "").startswith("scenario_") or d.priority for d in drafts)


def test_existing_weather_recommendations_still_work(db: Session) -> None:
    from app.api.schemas.weather_impact import (
        WeatherImpactAssessmentOut,
        WeatherImpactObservationKind,
        WeatherImpactScope,
        WeatherImpactSignalOut,
        WeatherImpactSignalType,
    )
    from app.services.operational_intelligence_service import OperationalIntelligenceService as OIS

    class StubWeather:
        def assess(self, *, zone_id=None, hours=48):
            if zone_id is not None:
                return WeatherImpactAssessmentOut(
                    location=_location(),
                    provider_meta=_meta(provider="test"),
                    forecast_provider_meta=_meta(provider="test"),
                    assessed_at=NOW,
                    scope=WeatherImpactScope.CORRIDOR,
                    zone_id=None,
                    forecast_hours=48,
                    overall_severity="none",
                    signals=[],
                    static_metadata_context=None,
                )
            return WeatherImpactAssessmentOut(
                location=_location(),
                provider_meta=_meta(provider="test"),
                forecast_provider_meta=_meta(provider="test"),
                assessed_at=NOW,
                scope=WeatherImpactScope.CORRIDOR,
                zone_id=None,
                forecast_hours=48,
                overall_severity="high",
                signals=[
                    WeatherImpactSignalOut(
                        signal_type=WeatherImpactSignalType.PRECIPITATION,
                        severity="high",
                        scope=WeatherImpactScope.CORRIDOR,
                        zone_id=None,
                        affected_asset_ids=[],
                        observation_kind=WeatherImpactObservationKind.CURRENT,
                        measured_value=8.0,
                        unit="mm",
                        threshold_rule="precipitation_mm >= 5.0",
                        explanation="Elevated rain.",
                        observed_at=NOW,
                        source="test",
                    )
                ],
                static_metadata_context=None,
            )

    intel = OIS(db)
    drafts = intel.weather_impact_rules(impact_service=StubWeather())
    assert drafts
    assert drafts[0].rule_code.startswith("weather_")


def test_recommendation_api_integration_with_stub(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = StubEnvironmentImpactService({None: _assessment([_signal(severity="high")])})

    original = RecommendationService._drafts

    def _drafts_with_env(self):
        state = self._scenario_repo.get_or_create()
        zone_scenarios = getattr(state, "zone_scenarios", None) or {}
        drafts = self._intel.generate(
            scenario_key=state.scenario_key,
            scenario_intensity=float(state.intensity),
            zone_scenarios=zone_scenarios,
        )
        drafts.extend(self._intel.environment_impact_rules(impact_service=stub))
        return drafts

    monkeypatch.setattr(RecommendationService, "_drafts", _drafts_with_env)
    response = client.get("/api/recommendations")
    assert response.status_code == 200
    env_recs = [r for r in response.json() if r.get("source") == "environment"]
    assert env_recs
    assert env_recs[0]["rule_code"].startswith("environment_")
    assert env_recs[0]["affected_asset"].startswith("environment-scope:")


def test_duplicate_prevention_same_rule_scope(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    stub = StubEnvironmentImpactService({None: _assessment([_signal(), _signal()])})
    drafts = intel.environment_impact_rules(impact_service=stub)
    # Two identical signals → two drafts with same identity components (engine lists both;
    # stable_id collision is accepted at API layer by last-write / list semantics).
    # Ensure rule_code+scope are identical so identity is stable, not timestamped.
    ids = {
        OperationalIntelligenceService.stable_id(d.rule_code, d.affected_asset) for d in drafts
    }
    assert len(ids) == 1


def test_maintenance_rules_still_callable(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.maintenance_prediction_rules([])
    assert drafts == []


def test_recommendations_api_unaffected_by_benign_env(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = StubEnvironmentImpactService({None: _assessment([])})

    def _drafts_benign(self):
        state = self._scenario_repo.get_or_create()
        zone_scenarios = getattr(state, "zone_scenarios", None) or {}
        drafts = self._intel.generate(
            scenario_key=state.scenario_key,
            scenario_intensity=float(state.intensity),
            zone_scenarios=zone_scenarios,
        )
        drafts.extend(self._intel.environment_impact_rules(impact_service=stub))
        return drafts

    monkeypatch.setattr(RecommendationService, "_drafts", _drafts_benign)
    response = client.get("/api/recommendations")
    assert response.status_code == 200
    env_recs = [r for r in response.json() if r.get("source") == "environment"]
    assert env_recs == []
