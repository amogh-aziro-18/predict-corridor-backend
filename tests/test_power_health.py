"""Phase 14.3B — Power rule-based health, alerts, recommendations."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.schemas.telemetry import TelemetryEnvelope
from app.db.seed import seed_all
from app.db.session import SessionLocal
from app.domain.enums import AlertStatus
from app.domain.models import Alert, Asset
from app.domain.repositories import AlertRepository
from app.main import app
from app.services.alert_service import AlertService
from app.services.dashboard_service import _aggregate_condition
from app.services.operational_intelligence_service import OperationalIntelligenceService
from app.services.recommendation_service import RecommendationService, _infer_recommendation_source
from app.services.telemetry_service import TelemetryService
from app.simulator.power.asset_simulator import PowerAssetSimulator
from app.simulator.power.scenarios import PowerScenarioMode
from app.utilities.power import health as power_health
from app.utilities.power.alert_producer import known_power_alert_codes, sync_power_asset_alerts
from app.utilities.power.seed_catalog import POWER_ASSET_IDS


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _seed() -> None:
    seed_all()


def test_healthy_metrics_produce_healthy_condition() -> None:
    result = power_health.evaluate_asset_condition(
        asset_id="XFMR-A-01",
        zone_id="zone-a",
        asset_type="transformer",
        metrics={
            "load_pct": 58.0,
            "oil_temp_c": 58.0,
            "voltage_kv": 132.0,
            "power_factor": 0.95,
        },
        nominal_voltage_kv=132.0,
    )
    assert result.condition == "healthy"
    assert result.alert_codes == []
    assert result.assessment_kind == "rule_based_condition_assessment"
    assert result.utility_kind == "power"
    assert result.health_score >= 90.0


def test_degraded_transformer_produces_high_risk_with_evidence() -> None:
    series = {
        "load_pct": [70.0, 74.0, 80.0, 86.0, 90.0],
        "oil_temp_c": [68.0, 72.0, 78.0, 84.0, 88.0],
        "voltage_kv": [132.0, 131.5, 130.0, 128.0, 126.0],
        "power_factor": [0.94, 0.93, 0.91, 0.90, 0.89],
    }
    metrics = {k: v[-1] for k, v in series.items()}
    result = power_health.evaluate_asset_condition(
        asset_id="XFMR-C-01",
        zone_id="zone-c",
        asset_type="transformer",
        metrics=metrics,
        series_by_metric=series,
        nominal_voltage_kv=132.0,
    )
    assert result.condition in {"warning", "high_risk", "critical"}
    assert result.alert_codes
    assert any(e.slope is not None for e in result.evidence)
    assert result.drivers
    # No fabricated ML labels.
    assert "prediction" not in result.assessment_kind
    assert "ml" not in result.assessment_kind.lower()


def test_stronger_stress_worse_than_mild() -> None:
    mild = power_health.evaluate_asset_condition(
        asset_id="XFMR-C-01",
        zone_id="zone-c",
        asset_type="transformer",
        metrics={"load_pct": 78.0, "oil_temp_c": 72.0, "voltage_kv": 130.0, "power_factor": 0.92},
        series_by_metric={
            "load_pct": [70.0, 74.0, 78.0],
            "oil_temp_c": [68.0, 70.0, 72.0],
        },
        nominal_voltage_kv=132.0,
    )
    strong = power_health.evaluate_asset_condition(
        asset_id="XFMR-D-01",
        zone_id="zone-d",
        asset_type="transformer",
        metrics={"load_pct": 99.0, "oil_temp_c": 96.0, "voltage_kv": 117.0, "power_factor": 0.80},
        series_by_metric={
            "load_pct": [80.0, 90.0, 99.0],
            "oil_temp_c": [80.0, 88.0, 96.0],
            "voltage_kv": [128.0, 122.0, 117.0],
        },
        nominal_voltage_kv=132.0,
    )
    rank = {"healthy": 0, "warning": 1, "high_risk": 2, "critical": 3, "unavailable": -1}
    assert rank[strong.condition] > rank[mild.condition]
    assert strong.health_score < mild.health_score


def test_health_ignores_zone_labels() -> None:
    """Same healthy metrics in zone-d must remain healthy — not label-driven."""
    result = power_health.evaluate_asset_condition(
        asset_id="XFMR-D-01",
        zone_id="zone-d",
        asset_type="transformer",
        metrics={
            "load_pct": 55.0,
            "oil_temp_c": 55.0,
            "voltage_kv": 132.0,
            "power_factor": 0.96,
        },
        nominal_voltage_kv=132.0,
    )
    assert result.condition == "healthy"
    assert result.alert_codes == []


def test_feeder_does_not_require_oil_temp() -> None:
    alerts = power_health.evaluate_alerts(
        {"load_pct": 92.0, "voltage_kv": 32.0, "power_factor": 0.90, "current_a": 400.0},
        asset_type="feeder",
        nominal_voltage_kv=33.0,
    )
    assert "oil_temp_high" not in alerts
    assert "load_high" in alerts


def test_unavailable_without_telemetry() -> None:
    result = power_health.evaluate_asset_condition(
        asset_id="XFMR-A-01",
        zone_id="zone-a",
        asset_type="transformer",
        metrics={},
    )
    assert result.condition == "unavailable"


def test_alert_dedup_not_spam_on_repeated_sync() -> None:
    _seed()
    db = SessionLocal()
    try:
        asset = db.query(Asset).filter(Asset.id == "XFMR-C-01").one()
        # Clear prior power alerts for asset
        for row in AlertRepository(db).list(asset_id=asset.id, utility_kind="power"):
            AlertRepository(db).delete(row)
        db.commit()

        codes = ["oil_temp_high", "load_high", "thermal_loading_stress"]
        sync_power_asset_alerts(db, asset=asset, codes=codes)
        db.commit()
        first = (
            db.query(Alert)
            .filter(
                Alert.asset_id == asset.id,
                Alert.utility_kind == "power",
                Alert.status == AlertStatus.OPEN.value,
            )
            .count()
        )
        assert first == len(codes)

        # Same codes again — set-diff upsert, no duplicate actives.
        sync_power_asset_alerts(db, asset=asset, codes=codes)
        db.commit()
        second = (
            db.query(Alert)
            .filter(
                Alert.asset_id == asset.id,
                Alert.utility_kind == "power",
                Alert.status == AlertStatus.OPEN.value,
            )
            .count()
        )
        assert second == first

        fingerprints = [
            a.fingerprint
            for a in db.query(Alert)
            .filter(Alert.asset_id == asset.id, Alert.utility_kind == "power")
            .all()
        ]
        assert len(fingerprints) == len(set(fingerprints))
    finally:
        db.close()


def test_known_power_codes_match_vocabulary() -> None:
    known = known_power_alert_codes()
    sample = power_health.evaluate_alerts(
        {
            "load_pct": 99.0,
            "oil_temp_c": 96.0,
            "voltage_kv": 117.0,
            "power_factor": 0.80,
            "frequency_hz": 49.2,
        },
        slopes={"oil_temp_c": 0.5, "load_pct": 1.0},
        asset_type="transformer",
        nominal_voltage_kv=132.0,
    )
    assert sample
    assert set(sample) <= known


def test_power_recommendations_reference_real_assets_and_rule_evidence() -> None:
    _seed()
    db = SessionLocal()
    try:
        # Inject clearly degraded transformer telemetry through TelemetryService.
        svc = TelemetryService(db)
        for metric_key, value, unit in (
            ("load_pct", 91.0, "%"),
            ("oil_temp_c", 86.0, "°C"),
            ("voltage_kv", 124.0, "kV"),
            ("power_factor", 0.87, "pf"),
            ("active_power_mw", 35.0, "MW"),
        ):
            # Multiple points so slopes exist in history.
            for offset in range(5):
                scaled = value - (4 - offset) * 2.0 if metric_key != "power_factor" else value + (4 - offset) * 0.01
                svc.persist_envelope(
                    asset_id="XFMR-D-01",
                    metric_key=metric_key,
                    envelope=TelemetryEnvelope(
                        value=scaled,
                        unit=unit,
                        quality="good",
                        metric_key=metric_key,
                    ),
                    utility_kind="power",
                    commit=False,
                )
        db.commit()

        asset = db.query(Asset).filter(Asset.id == "XFMR-D-01").one()
        assert asset.profile.get("condition") in {"warning", "high_risk", "critical"}
        assert asset.profile.get("assessment_kind") == "rule_based_condition_assessment"

        intel = OperationalIntelligenceService(db)
        drafts = intel.generate(scenario_key="normal", scenario_intensity=0.0)
        power_drafts = [d for d in drafts if d.rule_code and d.rule_code.startswith("power_")]
        assert power_drafts
        assert any(d.affected_asset == "XFMR-D-01" for d in power_drafts)
        chosen = next(d for d in power_drafts if d.affected_asset == "XFMR-D-01")
        assert "Rule-based" in chosen.description
        assert "ML" not in chosen.title
        assert "prediction" not in chosen.title.lower()
        assert chosen.affected_zone == "zone-d"
        assert 0.0 < chosen.confidence <= 1.0
        assert _infer_recommendation_source(chosen.rule_code) == "telemetry"
    finally:
        db.close()


def test_simulator_zones_diverge_into_conditions() -> None:
    """Run MIXED ticks; A/B stay healthier than C/D based on telemetry."""
    _seed()
    db = SessionLocal()
    try:
        sim = PowerAssetSimulator(db, seed=99, mode=PowerScenarioMode.MIXED)
        for _ in range(45):
            sim.persist_tick()

        conditions: dict[str, str] = {}
        health: dict[str, float] = {}
        for asset_id in POWER_ASSET_IDS:
            asset = db.query(Asset).filter(Asset.id == asset_id).one()
            conditions[asset_id] = str((asset.profile or {}).get("condition") or "unavailable")
            health[asset_id] = float(asset.health)

        rank = {"healthy": 0, "warning": 1, "high_risk": 2, "critical": 3, "unavailable": -1}
        by_zone: dict[str, list[str]] = {z: [] for z in ("zone-a", "zone-b", "zone-c", "zone-d")}
        for aid in POWER_ASSET_IDS:
            asset = db.query(Asset).filter(Asset.id == aid).one()
            by_zone[asset.zone_id].append(conditions[aid])

        def worst(zone: str) -> int:
            return max(rank.get(c, 0) for c in by_zone[zone])

        assert worst("zone-a") <= 1  # healthy or at most warning from noise
        assert worst("zone-b") <= 1
        assert worst("zone-c") >= 1
        assert worst("zone-d") >= worst("zone-c")
        assert worst("zone-d") >= 2  # high_risk or critical after sustained drift

        avg_h = {
            z: sum(
                health[a.id]
                for a in db.query(Asset).filter(
                    Asset.zone_id == z, Asset.utility_kind == "power"
                )
            )
            / 3.0
            for z in ("zone-a", "zone-b", "zone-c", "zone-d")
        }
        assert avg_h["zone-a"] > avg_h["zone-c"]
        assert avg_h["zone-b"] > avg_h["zone-d"]
        assert avg_h["zone-d"] <= avg_h["zone-c"] + 5.0

        # Persisted alerts exist for degrading zones, not fabricated by hand.
        active_power = AlertService(db).list_alerts(
            utility_kind="power", status=AlertStatus.OPEN.value
        )
        assert active_power
        zones_with_alerts = {a.zone_id for a in active_power}
        assert zones_with_alerts & {"zone-c", "zone-d"}
        ab = [a for a in active_power if a.zone_id in {"zone-a", "zone-b"}]
        cd = [a for a in active_power if a.zone_id in {"zone-c", "zone-d"}]
        assert len(cd) >= len(ab)
    finally:
        db.close()


def test_power_dashboard_endpoint(client: TestClient) -> None:
    _seed()
    db = SessionLocal()
    try:
        # Minimal telemetry so dashboard is not empty.
        svc = TelemetryService(db)
        svc.persist_envelope(
            asset_id="XFMR-A-01",
            metric_key="load_pct",
            envelope=TelemetryEnvelope(value=55.0, unit="%", quality="good", metric_key="load_pct"),
            utility_kind="power",
        )
    finally:
        db.close()

    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/dashboard/power" in paths
    resp = client.get("/api/dashboard/power")
    assert resp.status_code == 200
    body = resp.json()
    assert body["utility"] == "power"
    assert body["assessment_kind"] == "rule_based_condition_assessment"
    assert "zone_summary" in body
    assert body["asset_count"] == 12


def test_aggregate_condition_rule() -> None:
    assert _aggregate_condition(["healthy", "warning", "healthy"]) == "warning"
    assert _aggregate_condition(["healthy", "critical"]) == "critical"
    assert _aggregate_condition(["unavailable", "unavailable"]) == "unavailable"
    assert _aggregate_condition(["unavailable", "healthy"]) == "healthy"


def test_recommendations_via_service_include_power() -> None:
    _seed()
    db = SessionLocal()
    try:
        svc = TelemetryService(db)
        for metric_key, value, unit in (
            ("load_pct", 95.0, "%"),
            ("oil_temp_c", 90.0, "°C"),
            ("voltage_kv", 120.0, "kV"),
            ("power_factor", 0.85, "pf"),
            ("active_power_mw", 38.0, "MW"),
        ):
            svc.persist_envelope(
                asset_id="XFMR-D-01",
                metric_key=metric_key,
                envelope=TelemetryEnvelope(
                    value=value, unit=unit, quality="good", metric_key=metric_key
                ),
                utility_kind="power",
                commit=False,
            )
        db.commit()
        recs = RecommendationService(db).list_recommendations()
        rule_recs = [
            r
            for r in recs
            if (r.rule_code or "").startswith("power_")
            and not (r.rule_code or "").startswith("power_xgboost_")
        ]
        assert rule_recs
        assert all(r.affected_asset in POWER_ASSET_IDS for r in rule_recs)
        assert all("Rule-based" in r.description for r in rule_recs)
    finally:
        db.close()
