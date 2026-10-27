"""Phase 10A — resilience assessment + recommendation integration tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.schemas.environment_impact import (
    EnvironmentImpactAssessmentOut,
    EnvironmentImpactEvidenceOut,
    EnvironmentImpactSignalOut,
)
from app.api.schemas.environment_signal import EnvironmentObservationKind, EnvironmentSignalScope
from app.api.schemas.resilience import ResilienceAssessmentOut, ResilienceScope
from app.api.schemas.weather import WeatherLocationOut, WeatherProviderMetaOut
from app.api.schemas.weather_impact import (
    WeatherImpactAssessmentOut,
    WeatherImpactObservationKind,
    WeatherImpactScope,
    WeatherImpactSignalOut,
    WeatherImpactSignalType,
)
from app.core.errors import EnvironmentUnavailableError, WeatherUnavailableError
from app.db.session import SessionLocal
from app.domain.repositories.zone_repository import ZoneRepository
from app.main import app
from app.services.operational_intelligence_service import OperationalIntelligenceService
from app.services.recommendation_service import RecommendationService, _infer_recommendation_source
from app.services.resilience_assessment_service import ResilienceAssessmentService
from app.services.work_order_service import WorkOrderService
from app.api.schemas.work_order import WorkOrderCreate

NOW = datetime(2026, 9, 9, 16, 0, tzinfo=timezone.utc)


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


def _loc(**overrides: Any) -> WeatherLocationOut:
    base = {
        "latitude": 28.6,
        "longitude": 77.2,
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
        "provider": "test",
        "fetched_at": NOW,
        "cache_status": "fresh",
        "cache_expires_at": None,
        "stale": False,
    }
    base.update(overrides)
    return WeatherProviderMetaOut(**base)


def _wx_signal(**overrides: Any) -> WeatherImpactSignalOut:
    base = {
        "signal_type": WeatherImpactSignalType.PRECIPITATION,
        "severity": "high",
        "scope": WeatherImpactScope.CORRIDOR,
        "zone_id": None,
        "affected_asset_ids": [],
        "observation_kind": WeatherImpactObservationKind.CURRENT,
        "measured_value": 8.0,
        "unit": "mm",
        "threshold_rule": "precipitation_mm >= 5.0",
        "explanation": "Elevated rain.",
        "observed_at": NOW,
        "source": "test",
    }
    base.update(overrides)
    return WeatherImpactSignalOut(**base)


def _wx_assessment(signals: list[WeatherImpactSignalOut], **overrides: Any) -> WeatherImpactAssessmentOut:
    base = {
        "location": _loc(),
        "provider_meta": _meta(),
        "forecast_provider_meta": _meta(),
        "assessed_at": NOW,
        "scope": WeatherImpactScope.CORRIDOR,
        "zone_id": None,
        "forecast_hours": 24,
        "overall_severity": "high" if signals else "none",
        "signals": signals,
        "static_metadata_context": None,
    }
    base.update(overrides)
    return WeatherImpactAssessmentOut(**base)


def _env_signal(**overrides: Any) -> EnvironmentImpactSignalOut:
    base: dict[str, Any] = {
        "signal_type": "aqi",
        "severity": "high",
        "scope": EnvironmentSignalScope.CORRIDOR,
        "zone_id": None,
        "observation_kind": EnvironmentObservationKind.CURRENT,
        "measured_value": 160.0,
        "unit": "US AQI",
        "threshold_rule": "us_aqi >= 150",
        "explanation": "US AQI high.",
        "observed_at": NOW,
        "source": "test_aq",
        "metadata": {"aqi_standard": "us_aqi"},
    }
    base.update(overrides)
    return EnvironmentImpactSignalOut(**base)


def _env_assessment(
    signals: list[EnvironmentImpactSignalOut],
    **overrides: Any,
) -> EnvironmentImpactAssessmentOut:
    base: dict[str, Any] = {
        "location": _loc(),
        "provider_meta": _meta(provider="test_aq"),
        "assessed_at": NOW,
        "scope": EnvironmentSignalScope.CORRIDOR,
        "zone_id": None,
        "utility_kind": "water",
        "forecast_hours": 24,
        "observation_kinds": [EnvironmentObservationKind.CURRENT] if signals else [],
        "overall_severity": "high" if signals else "none",
        "signals": signals,
        "evidence": EnvironmentImpactEvidenceOut(
            note="test",
            threshold_basis="test",
            aqi_policy="us_aqi only",
        ),
        "note": None,
    }
    base.update(overrides)
    return EnvironmentImpactAssessmentOut(**base)


class StubWeather:
    def __init__(
        self,
        assessments: dict[str | None, WeatherImpactAssessmentOut] | None = None,
        *,
        fail: bool = False,
        fail_zones: set[str] | None = None,
    ) -> None:
        self._assessments = assessments or {None: _wx_assessment([])}
        self._fail = fail
        self._fail_zones = fail_zones or set()

    def assess(self, *, zone_id: str | None = None, hours: int = 48) -> WeatherImpactAssessmentOut:
        if self._fail and zone_id is None:
            raise WeatherUnavailableError("wx down")
        if zone_id is not None and zone_id in self._fail_zones:
            raise WeatherUnavailableError("zone wx down")
        if zone_id in self._assessments:
            return self._assessments[zone_id]
        return _wx_assessment(
            [],
            location=_loc(scope="zone", zone_id=zone_id),
            scope=WeatherImpactScope.ZONE,
            zone_id=zone_id,
        )


class StubEnvironment:
    def __init__(
        self,
        assessments: dict[str | None, EnvironmentImpactAssessmentOut] | None = None,
        *,
        fail: bool = False,
        fail_zones: set[str] | None = None,
    ) -> None:
        self._assessments = assessments or {None: _env_assessment([])}
        self._fail = fail
        self._fail_zones = fail_zones or set()

    def assess(
        self,
        *,
        zone_id: str | None = None,
        utility_kind: str | None = None,
        hours: int = 24,
    ) -> EnvironmentImpactAssessmentOut:
        if self._fail and zone_id is None:
            raise EnvironmentUnavailableError("env down")
        if zone_id is not None and zone_id in self._fail_zones:
            raise EnvironmentUnavailableError("zone env down")
        if zone_id in self._assessments:
            return self._assessments[zone_id]
        return _env_assessment(
            [],
            location=_loc(scope="zone", zone_id=zone_id),
            scope=EnvironmentSignalScope.ZONE,
            zone_id=zone_id,
        )


def _svc(
    db: Session,
    *,
    weather: StubWeather | None = None,
    environment: StubEnvironment | None = None,
) -> ResilienceAssessmentService:
    return ResilienceAssessmentService(
        db,
        weather_impact_service=weather or StubWeather(),
        environment_impact_service=environment or StubEnvironment(),
    )


def test_corridor_assessment(db: Session) -> None:
    svc = _svc(
        db,
        weather=StubWeather({None: _wx_assessment([_wx_signal()], overall_severity="high")}),
        environment=StubEnvironment({None: _env_assessment([])}),
    )
    a = svc.assess(zone_id=None)
    assert a.scope == ResilienceScope.CORRIDOR
    assert a.zone_id is None
    assert a.overall_posture in {"none", "low", "medium", "high", "critical"}
    assert a.overall_severity == a.overall_posture


def test_zone_assessment(db: Session) -> None:
    svc = _svc(
        db,
        weather=StubWeather(
            {
                None: _wx_assessment([]),
                "zone-c": _wx_assessment(
                    [_wx_signal(scope=WeatherImpactScope.ZONE, zone_id="zone-c")],
                    overall_severity="critical",
                    scope=WeatherImpactScope.ZONE,
                    zone_id="zone-c",
                ),
            }
        ),
    )
    a = svc.assess(zone_id="zone-c")
    assert a.scope == ResilienceScope.ZONE
    assert a.zone_id == "zone-c"


def test_dynamic_zone_discovery(db: Session) -> None:
    ctx = _svc(db).get_context()
    registered = {z.id for z in ZoneRepository(db).list()}
    assert {z.zone_id for z in ctx.zones} == registered


def test_utility_neutrality(db: Session) -> None:
    a = _svc(db).assess(utility_kind="power")
    assert a.utility_kind == "power"


def test_weather_driver(db: Session) -> None:
    a = _svc(
        db,
        weather=StubWeather({None: _wx_assessment([_wx_signal()], overall_severity="high")}),
    ).assess()
    weather = next(d for d in a.drivers if d.source == "weather" or str(d.source) == "weather")
    assert weather.severity == "high"
    assert weather.evidence["signal_count"] == 1


def test_environment_driver(db: Session) -> None:
    a = _svc(
        db,
        environment=StubEnvironment(
            {None: _env_assessment([_env_signal()], overall_severity="critical")}
        ),
    ).assess()
    env = next(d for d in a.drivers if str(d.source) == "environment")
    assert env.severity == "critical"
    assert env.evidence.get("aqi_standard") == "us_aqi"
    assert "US AQI" in env.explanation or "us_aqi" in env.explanation


def test_scenario_driver(db: Session) -> None:
    a = _svc(db).assess()
    scenario = next(d for d in a.drivers if str(d.source) == "scenario")
    assert "scenario_key" in scenario.evidence


def test_asset_posture_evidence(db: Session) -> None:
    a = _svc(db).assess()
    asset = next(d for d in a.drivers if str(d.source) == "asset_posture")
    assert "asset_count" in asset.evidence
    assert asset.evidence.get("static_metadata_only") is True
    assert "seed metadata" in asset.explanation.lower() or "static" in asset.explanation.lower()


def test_high_criticality_no_backup_count(db: Session) -> None:
    a = _svc(db).assess()
    asset = next(d for d in a.drivers if str(d.source) == "asset_posture")
    assert "high_criticality_no_backup" in asset.evidence
    assert isinstance(asset.evidence["high_criticality_no_backup"], int)


def test_severity_ordering() -> None:
    assert ResilienceAssessmentService._rank("none") < ResilienceAssessmentService._rank("low")
    assert ResilienceAssessmentService._rank("low") < ResilienceAssessmentService._rank("medium")
    assert ResilienceAssessmentService._rank("medium") < ResilienceAssessmentService._rank("high")
    assert ResilienceAssessmentService._rank("high") < ResilienceAssessmentService._rank("critical")


def test_one_step_asset_posture_elevation(db: Session) -> None:
    # Force medium external stress + enough no-backup critical assets via monkeypatch stats path
    svc = _svc(
        db,
        weather=StubWeather({None: _wx_assessment([_wx_signal(severity="medium")], overall_severity="medium")}),
        environment=StubEnvironment({None: _env_assessment([])}),
    )
    original = svc._asset_posture_driver

    def _elevating_assets(**kwargs):
        driver, stats = original(**kwargs)
        stats = {
            **stats,
            "high_criticality_no_backup": 2,
            "high_criticality": 2,
            "asset_count": max(stats["asset_count"], 2),
        }
        return driver, stats

    svc._asset_posture_driver = _elevating_assets  # type: ignore[method-assign]
    a = svc.assess()
    assert a.overall_posture == "high"
    assert any("elevated one step" in e.lower() for e in a.evidence)


def test_critical_cap(db: Session) -> None:
    svc = _svc(
        db,
        weather=StubWeather(
            {None: _wx_assessment([_wx_signal(severity="critical")], overall_severity="critical")}
        ),
    )
    original = svc._asset_posture_driver

    def _elevating_assets(**kwargs):
        driver, stats = original(**kwargs)
        stats = {**stats, "high_criticality_no_backup": 5}
        return driver, stats

    svc._asset_posture_driver = _elevating_assets  # type: ignore[method-assign]
    a = svc.assess()
    assert a.overall_posture == "critical"


def test_normal_scenario(db: Session) -> None:
    # Ensure scenario state is normal for this session
    from app.domain.repositories.scenario_repository import ScenarioRepository
    from app.scenarios.definitions import ScenarioKey

    row = ScenarioRepository(db).get_or_create()
    row.scenario_key = ScenarioKey.NORMAL.value
    row.intensity = 0.0
    row.zone_scenarios = {}
    db.add(row)
    db.flush()

    a = _svc(db).assess()
    scenario = next(d for d in a.drivers if str(d.source) == "scenario")
    assert scenario.severity == "none"


def test_non_normal_scenario(db: Session) -> None:
    from app.domain.repositories.scenario_repository import ScenarioRepository
    from app.scenarios.definitions import ScenarioKey

    row = ScenarioRepository(db).get_or_create()
    row.scenario_key = ScenarioKey.POWER_OUTAGE.value
    row.intensity = 0.5
    row.zone_scenarios = {}
    db.add(row)
    db.flush()

    a = _svc(
        db,
        weather=StubWeather({None: _wx_assessment([])}),
        environment=StubEnvironment({None: _env_assessment([])}),
    ).assess()
    scenario = next(d for d in a.drivers if str(d.source) == "scenario")
    assert scenario.severity == "critical"
    assert a.overall_posture == "critical"


def test_weather_unavailable(db: Session) -> None:
    a = _svc(db, weather=StubWeather(fail=True)).assess()
    assert a.inputs_available.weather_available is False
    assert not any(str(d.source) == "weather" for d in a.drivers)


def test_environment_unavailable(db: Session) -> None:
    a = _svc(db, environment=StubEnvironment(fail=True)).assess()
    assert a.inputs_available.environment_available is False
    assert not any(str(d.source) == "environment" for d in a.drivers)


def test_both_unavailable(db: Session) -> None:
    from app.domain.repositories.scenario_repository import ScenarioRepository
    from app.scenarios.definitions import ScenarioKey

    row = ScenarioRepository(db).get_or_create()
    row.scenario_key = ScenarioKey.NORMAL.value
    row.intensity = 0.0
    row.zone_scenarios = {}
    db.add(row)
    db.flush()

    a = _svc(db, weather=StubWeather(fail=True), environment=StubEnvironment(fail=True)).assess()
    assert a.inputs_available.weather_available is False
    assert a.inputs_available.environment_available is False
    # Scenario none + assets may still yield none/low — never invent medium
    assert a.overall_posture in {"none", "low"}


def test_stale_weather(db: Session) -> None:
    a = _svc(
        db,
        weather=StubWeather(
            {
                None: _wx_assessment(
                    [_wx_signal()],
                    overall_severity="high",
                    provider_meta=_meta(stale=True, cache_status="stale"),
                )
            }
        ),
    ).assess()
    assert a.inputs_stale is True
    weather = next(d for d in a.drivers if str(d.source) == "weather")
    assert weather.evidence.get("stale") is True


def test_stale_environment(db: Session) -> None:
    a = _svc(
        db,
        environment=StubEnvironment(
            {
                None: _env_assessment(
                    [_env_signal()],
                    overall_severity="high",
                    provider_meta=_meta(provider="aq", stale=True),
                    note="Returning stale cached air-quality observations.",
                )
            }
        ),
    ).assess()
    assert a.inputs_stale is True


def test_insufficient_inputs(db: Session) -> None:
    from app.domain.repositories.scenario_repository import ScenarioRepository
    from app.scenarios.definitions import ScenarioKey

    row = ScenarioRepository(db).get_or_create()
    row.scenario_key = ScenarioKey.NORMAL.value
    row.zone_scenarios = {}
    db.add(row)
    db.flush()

    a = _svc(
        db,
        weather=StubWeather(fail=True),
        environment=StubEnvironment(fail=True),
    ).assess(utility_kind="nonexistent_utility_xyz")
    assert a.overall_posture in {"none", "low"}
    assert a.note is not None


def test_no_fabricated_measurements(db: Session) -> None:
    a = _svc(db).assess()
    blob = a.model_dump()
    assert "spare" not in str(blob).lower()
    assert "recovery eta" not in str(blob).lower()
    assert "₹" not in str(blob)


def test_no_asset_level_aq_values(db: Session) -> None:
    a = _svc(
        db,
        environment=StubEnvironment({None: _env_assessment([_env_signal()])}),
    ).assess()
    env = next(d for d in a.drivers if str(d.source) == "environment")
    assert "affected_asset_ids" not in env.evidence
    assert "asset-" not in str(env.evidence).lower()


def test_multiple_weather_signals_one_driver(db: Session) -> None:
    signals = [
        _wx_signal(signal_type=WeatherImpactSignalType.PRECIPITATION, severity="high"),
        _wx_signal(signal_type=WeatherImpactSignalType.WIND_SPEED, severity="medium"),
        _wx_signal(signal_type=WeatherImpactSignalType.PRESSURE, severity="critical"),
    ]
    a = _svc(
        db,
        weather=StubWeather({None: _wx_assessment(signals, overall_severity="critical")}),
    ).assess()
    weather_drivers = [d for d in a.drivers if str(d.source) == "weather"]
    assert len(weather_drivers) == 1
    assert weather_drivers[0].evidence["signal_count"] == 3


def test_multiple_environment_signals_one_driver(db: Session) -> None:
    signals = [
        _env_signal(signal_type="aqi", severity="critical"),
        _env_signal(signal_type="pm10", severity="critical", metadata={"provider_field": "pm10"}),
        _env_signal(signal_type="pm25", severity="high", metadata={"provider_field": "pm2_5"}),
    ]
    a = _svc(
        db,
        environment=StubEnvironment(
            {None: _env_assessment(signals, overall_severity="critical")}
        ),
    ).assess()
    env_drivers = [d for d in a.drivers if str(d.source) == "environment"]
    assert len(env_drivers) == 1
    assert env_drivers[0].evidence["signal_count"] == 3


def _assessment_for_rec(**overrides: Any) -> ResilienceAssessmentOut:
    from app.api.schemas.resilience import (
        ResilienceDriverOut,
        ResilienceInputsAvailabilityOut,
    )

    base = {
        "scope": ResilienceScope.CORRIDOR,
        "zone_id": None,
        "utility_kind": "water",
        "overall_posture": "high",
        "overall_severity": "high",
        "drivers": [
            ResilienceDriverOut(
                source="weather",
                severity="high",
                explanation="Weather high",
                evidence={},
            ),
            ResilienceDriverOut(
                source="asset_posture",
                severity="low",
                explanation="Assets",
                evidence={
                    "asset_count": 10,
                    "high_criticality": 3,
                    "high_criticality_no_backup": 2,
                },
            ),
        ],
        "evidence": ["Weather high"],
        "assessed_at": NOW,
        "forecast_hours": 24,
        "inputs_available": ResilienceInputsAvailabilityOut(
            weather_available=True,
            environment_available=True,
            scenario_available=True,
            assets_available=True,
        ),
        "inputs_stale": False,
        "note": "POC",
    }
    base.update(overrides)
    return ResilienceAssessmentOut(**base)


class StubResilience:
    def __init__(self, corridor: ResilienceAssessmentOut, zones: list[ResilienceAssessmentOut] | None = None):
        self._corridor = corridor
        self._zones = zones or []

    def get_context(self, *, utility_kind: str | None = None, hours: int = 24):
        from app.api.schemas.resilience import ResilienceContextOut

        return ResilienceContextOut(
            utility_kind=self._corridor.utility_kind,
            assessed_at=NOW,
            forecast_hours=hours,
            corridor=self._corridor,
            zones=self._zones,
            note="test",
        )


def test_high_resilience_recommendation(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.resilience_rules(
        resilience_service=StubResilience(_assessment_for_rec(overall_posture="high", overall_severity="high"))
    )
    assert len(drafts) == 1
    assert drafts[0].rule_code == "resilience_posture"
    assert drafts[0].priority == "high"


def test_critical_resilience_recommendation(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.resilience_rules(
        resilience_service=StubResilience(
            _assessment_for_rec(overall_posture="critical", overall_severity="critical")
        )
    )
    assert drafts[0].priority == "critical"


def test_medium_no_resilience_recommendation(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.resilience_rules(
        resilience_service=StubResilience(
            _assessment_for_rec(overall_posture="medium", overall_severity="medium")
        )
    )
    assert drafts == []


def test_low_no_resilience_recommendation(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.resilience_rules(
        resilience_service=StubResilience(_assessment_for_rec(overall_posture="low", overall_severity="low"))
    )
    assert drafts == []


def test_max_one_corridor_recommendation(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.resilience_rules(
        resilience_service=StubResilience(
            _assessment_for_rec(overall_posture="critical", overall_severity="critical")
        )
    )
    corridor = [d for d in drafts if d.affected_asset == "resilience-scope:corridor"]
    assert len(corridor) == 1


def test_max_one_recommendation_per_zone(db: Session) -> None:
    zone = _assessment_for_rec(
        scope=ResilienceScope.ZONE,
        zone_id="zone-c",
        overall_posture="high",
        overall_severity="high",
    )
    intel = OperationalIntelligenceService(db)
    drafts = intel.resilience_rules(
        resilience_service=StubResilience(
            _assessment_for_rec(overall_posture="none", overall_severity="none"),
            zones=[zone],
        )
    )
    zone_drafts = [d for d in drafts if d.affected_asset == "resilience-scope:zone:zone-c"]
    assert len(zone_drafts) == 1


def test_stable_recommendation_id(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    stub = StubResilience(_assessment_for_rec(overall_posture="high", overall_severity="high"))
    ids = {
        OperationalIntelligenceService.stable_id(d.rule_code, d.affected_asset)
        for _ in range(3)
        for d in intel.resilience_rules(resilience_service=stub)
    }
    assert len(ids) == 1


def test_no_timestamp_identity(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.resilience_rules(
        resilience_service=StubResilience(_assessment_for_rec(overall_posture="high", overall_severity="high"))
    )
    assert "2026" not in drafts[0].rule_code
    assert "T16" not in drafts[0].affected_asset


def test_correct_source_resilience() -> None:
    assert _infer_recommendation_source("resilience_posture") == "resilience"
    assert _infer_recommendation_source("weather_precipitation_current") == "weather"
    assert _infer_recommendation_source("environment_aqi_current") == "environment"


def test_no_fake_asset_id(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.resilience_rules(
        resilience_service=StubResilience(_assessment_for_rec(overall_posture="high", overall_severity="high"))
    )
    assert drafts[0].affected_asset.startswith("resilience-scope:")


def test_scope_only_not_wo_eligible(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.resilience_rules(
        resilience_service=StubResilience(_assessment_for_rec(overall_posture="high", overall_severity="high"))
    )
    scope_key = drafts[0].affected_asset
    with pytest.raises(Exception):
        WorkOrderService(db).create_work_order(
            WorkOrderCreate(
                asset_id=scope_key,
                title="from resilience",
                priority="high",
                source="recommendation",
            )
        )


def test_resilience_api(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    stub = StubResilience(_assessment_for_rec(overall_posture="medium", overall_severity="medium"))

    def _factory(db, **kwargs):
        return MagicMock(get_context=lambda **kw: stub.get_context(**kw))

    monkeypatch.setattr("app.api.routers.resilience.ResilienceAssessmentService", _factory)
    response = client.get("/api/resilience")
    assert response.status_code == 200
    body = response.json()
    assert "corridor" in body
    assert "zones" in body
    assert body["corridor"]["overall_posture"] == "medium"
    assert "drivers" in body["corridor"]
    assert body["utility_kind"] == "water"


def test_api_schema_fields(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    stub = StubResilience(_assessment_for_rec())
    monkeypatch.setattr(
        "app.api.routers.resilience.ResilienceAssessmentService",
        lambda db, **kwargs: MagicMock(get_context=lambda **kw: stub.get_context(**kw)),
    )
    body = client.get("/api/resilience").json()
    assert "inputs_available" in body["corridor"]
    assert "drivers" in body["corridor"]
    assert "overall_severity" in body["corridor"]


def test_recommendation_api_includes_resilience_source(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = StubResilience(_assessment_for_rec(overall_posture="critical", overall_severity="critical"))

    def _drafts(self):
        return self._intel.resilience_rules(resilience_service=stub)

    monkeypatch.setattr(RecommendationService, "_drafts", _drafts)
    response = client.get("/api/recommendations")
    assert response.status_code == 200
    res = [r for r in response.json() if r.get("source") == "resilience"]
    assert res
    assert res[0]["rule_code"] == "resilience_posture"


def test_weather_recommendations_unchanged_path(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    # resilience stub should not alter weather_impact_rules method existence
    assert callable(intel.weather_impact_rules)


def test_environment_recommendations_unchanged_path(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    assert callable(intel.environment_impact_rules)


def test_telemetry_scenario_maintenance_paths(db: Session) -> None:
    intel = OperationalIntelligenceService(db)
    drafts = intel.generate(
        scenario_key="normal_operations",
        scenario_intensity=0.0,
        zone_scenarios={},
    )
    assert isinstance(drafts, list)
    assert intel.maintenance_prediction_rules([]) == []


def test_gis_context_still_available(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_environment_weather_api import RecordingWeatherProvider

    monkeypatch.setattr(
        "app.services.weather_service.build_weather_provider",
        lambda settings=None: RecordingWeatherProvider(),
    )
    monkeypatch.setattr(
        "app.services.gis_service.EnvironmentImpactService.assess",
        lambda self, **kwargs: _env_assessment([]),
    )
    response = client.get("/api/gis/context", params={"hours": 6})
    assert response.status_code == 200
    body = response.json()
    assert "corridor" in body
    assert "environment" in body
