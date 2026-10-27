"""Phase 17.12 — Communications cross-utility + environment + resilience hardening.

Locks:
- FDR-A-01 → POP-A-01 supplies exposure (hypothetical, non-mutating)
- Weather static metadata aggregates enabled utilities including Communications
- Weather path does not invent Comm telemetry mutation
- Resilience diagnostic exposes Water/Power/Gas/Communications slices
- Corridor continues four live utilities
- Recommendation sources remain distinct (cross_utility vs communications_*)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.api.schemas.weather import (
    CurrentWeatherOut,
    ForecastPointOut,
    WeatherForecastOut,
    WeatherLocationOut,
    WeatherProviderMetaOut,
)
from app.db.seed import seed_all
from app.db.session import SessionLocal
from app.domain.models import Asset, Alert, Telemetry, WorkOrder
from app.integrations.weather.location_constants import (
    WEATHER_LOCATION_METHOD_CENTROID_OF_ASSETS,
)
from app.main import app
from app.services.cross_utility_impact_service import CrossUtilityImpactService
from app.services.recommendation_service import RecommendationService
from app.services.resilience_diagnostic_service import ResilienceDiagnosticService
from app.services.weather_impact_service import WeatherImpactService

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


def _client() -> TestClient:
    return TestClient(app)


def _set_condition(db, asset_id: str, condition: str) -> None:
    asset = db.get(Asset, asset_id)
    assert asset is not None
    profile = dict(asset.profile or {})
    profile["condition"] = condition
    asset.profile = profile
    db.add(asset)
    db.commit()


class _FakeWeatherService:
    def __init__(self) -> None:
        loc = WeatherLocationOut(
            latitude=28.6,
            longitude=77.2,
            timezone="Asia/Kolkata",
            scope="zone",
            zone_id="zone-a",
            asset_count=8,
            utility_kind="water",
            method=WEATHER_LOCATION_METHOD_CENTROID_OF_ASSETS,
        )
        meta = WeatherProviderMetaOut(
            provider="test",
            fetched_at=NOW,
            cache_status="fresh",
            cache_expires_at=NOW + timedelta(minutes=10),
            stale=False,
        )
        self._current = CurrentWeatherOut(
            location=loc,
            provider_meta=meta,
            observed_at=NOW,
            temperature_c=28.0,
            relative_humidity_pct=55.0,
            precipitation_mm=0.0,
            wind_speed_kmh=12.0,
            wind_direction_deg=180.0,
            pressure_hpa=1013.0,
            weather_code=0,
        )
        self._forecast = WeatherForecastOut(
            location=loc,
            provider_meta=meta,
            hourly=[
                ForecastPointOut(
                    timestamp=NOW + timedelta(hours=1),
                    temperature_c=28.0,
                    relative_humidity_pct=55.0,
                    precipitation_mm=0.0,
                    precipitation_probability_pct=5.0,
                    wind_speed_kmh=12.0,
                    wind_gusts_kmh=18.0,
                    pressure_hpa=1013.0,
                    weather_code=0,
                )
            ],
        )

    def get_current(self, *, zone_id: str | None = None) -> CurrentWeatherOut:
        del zone_id
        return self._current

    def get_forecast(
        self, *, zone_id: str | None = None, hours: int = 48
    ) -> WeatherForecastOut:
        del zone_id, hours
        return self._forecast


def test_fdr_a_impact_includes_pop_hypothetical_non_mutating() -> None:
    seed_all()
    db = SessionLocal()
    try:
        tel_before = db.query(Telemetry).count()
        alert_before = db.query(Alert).count()
        wo_before = db.query(WorkOrder).count()
        pop = db.get(Asset, "POP-A-01")
        assert pop is not None
        pop_health = pop.health
        pop_status = pop.status

        body = CrossUtilityImpactService(db).assess_asset_impact(
            asset_id="FDR-A-01",
            condition="unavailable",
        )
        assert body["mutates_operational_state"] is False
        ids = {a["asset_id"] for a in body["affected_assets"]}
        assert "POP-A-01" in ids
        assert "communications" in set(body["affected_utilities"])
        assert all(a["posture"] == "potentially_affected" for a in body["affected_assets"])

        db.expire_all()
        pop2 = db.get(Asset, "POP-A-01")
        assert pop2 is not None
        assert pop2.health == pop_health
        assert pop2.status == pop_status
        assert db.query(Telemetry).count() == tel_before
        assert db.query(Alert).count() == alert_before
        assert db.query(WorkOrder).count() == wo_before
    finally:
        db.close()


def test_cross_utility_oi_lists_communications_dependent() -> None:
    seed_all()
    db = SessionLocal()
    try:
        _set_condition(db, "FDR-A-01", "critical")
        _set_condition(db, "FDR-C-01", "healthy")
        recs = RecommendationService(db).list_recommendations()
        xu = [
            r
            for r in recs
            if r.source == "cross_utility" and r.affected_asset == "FDR-A-01"
        ]
        assert len(xu) == 1
        hit = xu[0]
        text = f"{hit.description} {hit.recommendation}".lower()
        assert "pop-a-01" in text
        assert "communications" in text
        assert hit.rule_code == "cross_utility_dependency_impact"
        assert not (hit.rule_code or "").startswith("communications_")
        ctx = hit.context or {}
        assert ctx.get("kind") == "cross_utility_dependency_impact"
        assert ctx.get("impact", {}).get("hypothetical") is True
    finally:
        _set_condition(db, "FDR-A-01", "healthy")
        db.close()


def test_weather_static_metadata_includes_communications_assets() -> None:
    seed_all()
    db = SessionLocal()
    try:
        assessment = WeatherImpactService(
            db, weather_service=_FakeWeatherService()  # type: ignore[arg-type]
        ).assess(zone_id="zone-a")
        ctx = assessment.static_metadata_context
        assert ctx is not None
        assert ctx.utility_kind == "all_enabled"
        assert ctx.asset_count > 0
        assert "Weather does not mutate simulator telemetry" in ctx.note
        assert all(s.affected_asset_ids == [] for s in assessment.signals)
    finally:
        db.close()


def test_resilience_diagnostic_four_utility_slices() -> None:
    seed_all()
    db = SessionLocal()
    try:
        _set_condition(db, "FDR-A-01", "critical")
        _set_condition(db, "FDR-C-01", "healthy")
        diag = ResilienceDiagnosticService(db).get_diagnostic(hours=24)
        ids = {s.id for s in diag.slices}
        assert {"water", "power", "gas", "communications"}.issubset(ids)
        comm = next(s for s in diag.slices if s.id == "communications")
        assert (comm.metrics or {}).get("asset_count", 0) >= 1
        assert "dependency_exposure" in ids
        dep = next(s for s in diag.slices if s.id == "dependency_exposure")
        affected = set(dep.metrics.get("affected_asset_ids") or [])
        utils = {str(u).lower() for u in (dep.metrics.get("affected_utilities") or [])}
        assert "POP-A-01" in affected
        assert "communications" in utils
        assert dep.metrics.get("hypothetical") is True
    finally:
        _set_condition(db, "FDR-A-01", "healthy")
        db.close()


def test_corridor_four_live_utilities_and_dependency_exposure_api() -> None:
    seed_all()
    client = _client()
    body = client.get("/api/dashboard/corridor").json()
    utils = {u["utility"] for u in body["utilities"]}
    assert {"water", "power", "gas", "communications"}.issubset(utils)

    db = SessionLocal()
    try:
        _set_condition(db, "FDR-A-01", "critical")
        _set_condition(db, "FDR-C-01", "healthy")
    finally:
        db.close()

    impact = client.post(
        "/api/impact/asset",
        json={"asset_id": "FDR-A-01", "condition": "critical"},
    )
    assert impact.status_code == 200, impact.text
    payload = impact.json()
    assert payload["mutates_operational_state"] is False
    assert "POP-A-01" in {a["asset_id"] for a in payload["affected_assets"]}
