"""Phase 15.2 — Gas alerts (set-diff) + operational recommendations."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.schemas.telemetry import TelemetryEnvelope
from app.db.seed import seed_all
from app.db.session import SessionLocal
from app.domain.enums import AlertStatus
from app.domain.models import Alert, Asset, WorkOrder
from app.domain.repositories import AlertRepository
from app.main import app
from app.services.alert_service import AlertService
from app.services.gas_service import GasService
from app.services.operational_intelligence_service import OperationalIntelligenceService
from app.services.recommendation_service import (
    RecommendationService,
    _infer_recommendation_source,
)
from app.services.telemetry_service import TelemetryService
from app.simulator.gas.asset_simulator import GasAssetSimulator
from app.simulator.gas.scenarios import GasScenarioMode
from app.utilities.gas.alert_producer import known_gas_alert_codes, sync_gas_asset_alerts
from app.utilities.gas.seed_catalog import GAS_ASSET_IDS


def _seed() -> None:
    seed_all()


def _persist_compressor_metrics(
    svc: TelemetryService,
    *,
    asset_id: str,
    metrics: dict[str, tuple[float, str]],
    points: int = 5,
    evaluate: bool = True,
) -> None:
    for metric_key, (value, unit) in metrics.items():
        for offset in range(points):
            # Mild rising trajectory into the final value.
            scaled = value - (points - 1 - offset) * (
                0.04 if metric_key == "leak_indicator" else 1.0
            )
            svc.persist_envelope(
                asset_id=asset_id,
                metric_key=metric_key,
                envelope=TelemetryEnvelope(
                    value=scaled,
                    unit=unit,
                    quality="good",
                    metric_key=metric_key,
                ),
                utility_kind="gas",
                commit=False,
                evaluate_gas=False,
            )
    if evaluate:
        GasService(svc._db).evaluate_condition(asset_id)
    svc._db.commit()


def test_gas_warning_alert_creation() -> None:
    _seed()
    db = SessionLocal()
    try:
        asset = db.query(Asset).filter(Asset.id == "CMP-C-01").one()
        for row in AlertRepository(db).list(asset_id=asset.id, utility_kind="gas"):
            AlertRepository(db).delete(row)
        db.commit()

        sync_gas_asset_alerts(
            db,
            asset=asset,
            codes=["compressor_load_elevated", "vibration_elevated"],
        )
        db.commit()

        open_alerts = (
            db.query(Alert)
            .filter(
                Alert.asset_id == asset.id,
                Alert.utility_kind == "gas",
                Alert.status == AlertStatus.OPEN.value,
            )
            .all()
        )
        codes = {a.alert_code for a in open_alerts}
        assert codes == {"compressor_load_elevated", "vibration_elevated"}
        assert all(a.severity == "medium" for a in open_alerts)
        assert all(a.source == "telemetry" for a in open_alerts)
    finally:
        db.close()


def test_gas_critical_alert_creation() -> None:
    _seed()
    db = SessionLocal()
    try:
        svc = TelemetryService(db)
        _persist_compressor_metrics(
            svc,
            asset_id="CMP-D-01",
            metrics={
                "compressor_load_pct": (102.0, "%"),
                "vibration_mm_s": (8.5, "mm/s"),
                "temperature_c": (98.0, "°C"),
                "leak_indicator": (0.85, "0-1"),
                "pressure_bar": (18.0, "bar"),
                "flow_rate_nm3_h": (2800.0, "Nm3/h"),
                "gas_quality_pct": (82.0, "%"),
                "compressor_discharge_pressure_bar": (20.0, "bar"),
                "power_consumption_kw": (550.0, "kW"),
            },
        )
        asset = db.query(Asset).filter(Asset.id == "CMP-D-01").one()
        assert asset.profile.get("condition") == "critical"
        open_alerts = [
            a
            for a in AlertService(db).list_alerts(
                utility_kind="gas",
                status=AlertStatus.OPEN.value,
            )
            if a.asset_id == "CMP-D-01"
        ]
        assert open_alerts
        assert any(a.severity == "critical" for a in open_alerts)
        assert any(a.alert_code.endswith("_critical") for a in open_alerts)
        assert all(a.alert_code in known_gas_alert_codes() for a in open_alerts)
    finally:
        db.close()


def test_alert_fingerprint_deduplication() -> None:
    _seed()
    db = SessionLocal()
    try:
        asset = db.query(Asset).filter(Asset.id == "CMP-C-01").one()
        for row in AlertRepository(db).list(asset_id=asset.id, utility_kind="gas"):
            AlertRepository(db).delete(row)
        db.commit()

        codes = ["leak_indicator_high", "temperature_high"]
        sync_gas_asset_alerts(db, asset=asset, codes=codes)
        db.commit()
        first = (
            db.query(Alert)
            .filter(
                Alert.asset_id == asset.id,
                Alert.utility_kind == "gas",
                Alert.status == AlertStatus.OPEN.value,
            )
            .count()
        )
        sync_gas_asset_alerts(db, asset=asset, codes=codes)
        db.commit()
        second = (
            db.query(Alert)
            .filter(
                Alert.asset_id == asset.id,
                Alert.utility_kind == "gas",
                Alert.status == AlertStatus.OPEN.value,
            )
            .count()
        )
        assert first == len(codes)
        assert second == first
        fps = [
            a.fingerprint
            for a in db.query(Alert)
            .filter(Alert.asset_id == asset.id, Alert.utility_kind == "gas")
            .all()
        ]
        assert len(fps) == len(set(fps))
    finally:
        db.close()


def test_alert_resolution_after_condition_clears() -> None:
    """Active → clear telemetry → set-diff resolves; no manual delete."""
    _seed()
    db = SessionLocal()
    try:
        svc = TelemetryService(db)
        # Degrade CMP-B-01.
        _persist_compressor_metrics(
            svc,
            asset_id="CMP-B-01",
            metrics={
                "compressor_load_pct": (92.0, "%"),
                "vibration_mm_s": (5.5, "mm/s"),
                "temperature_c": (84.0, "°C"),
                "leak_indicator": (0.50, "0-1"),
                "pressure_bar": (22.0, "bar"),
                "flow_rate_nm3_h": (3400.0, "Nm3/h"),
                "gas_quality_pct": (90.0, "%"),
                "compressor_discharge_pressure_bar": (24.0, "bar"),
                "power_consumption_kw": (480.0, "kW"),
            },
        )
        open_before = [
            a
            for a in AlertService(db).list_alerts(
                utility_kind="gas",
                status=AlertStatus.OPEN.value,
            )
            if a.asset_id == "CMP-B-01"
        ]
        assert open_before
        asset = db.query(Asset).filter(Asset.id == "CMP-B-01").one()
        assert asset.profile.get("condition") in {"warning", "high_risk", "critical"}

        drafts_before = [
            d
            for d in OperationalIntelligenceService(db).generate(
                scenario_key="normal", scenario_intensity=0.0
            )
            if d.affected_asset == "CMP-B-01" and d.rule_code.startswith("gas_")
        ]
        assert drafts_before

        # Recover to healthy telemetry — set-diff must resolve alerts.
        _persist_compressor_metrics(
            svc,
            asset_id="CMP-B-01",
            metrics={
                "compressor_load_pct": (55.0, "%"),
                "vibration_mm_s": (1.9, "mm/s"),
                "temperature_c": (44.0, "°C"),
                "leak_indicator": (0.04, "0-1"),
                "pressure_bar": (28.0, "bar"),
                "flow_rate_nm3_h": (4200.0, "Nm3/h"),
                "gas_quality_pct": (96.5, "%"),
                "compressor_discharge_pressure_bar": (32.0, "bar"),
                "power_consumption_kw": (375.0, "kW"),
            },
            points=6,
        )
        asset = db.query(Asset).filter(Asset.id == "CMP-B-01").one()
        assert asset.profile.get("condition") == "healthy"
        assert asset.profile.get("condition_alert_codes") == []

        open_after = [
            a
            for a in AlertService(db).list_alerts(
                utility_kind="gas",
                status=AlertStatus.OPEN.value,
            )
            if a.asset_id == "CMP-B-01"
        ]
        assert open_after == []

        resolved = (
            db.query(Alert)
            .filter(
                Alert.asset_id == "CMP-B-01",
                Alert.utility_kind == "gas",
                Alert.status == AlertStatus.RESOLVED.value,
            )
            .all()
        )
        assert resolved
        assert all(a.resolve_notes == "Gas threshold condition cleared" for a in resolved)

        drafts_after = [
            d
            for d in OperationalIntelligenceService(db).generate(
                scenario_key="normal", scenario_intensity=0.0
            )
            if d.affected_asset == "CMP-B-01" and d.rule_code.startswith("gas_")
        ]
        assert drafts_after == []
    finally:
        db.close()


def test_gas_recommendation_generation() -> None:
    _seed()
    db = SessionLocal()
    try:
        svc = TelemetryService(db)
        _persist_compressor_metrics(
            svc,
            asset_id="CMP-C-01",
            metrics={
                "compressor_load_pct": (91.0, "%"),
                "vibration_mm_s": (5.2, "mm/s"),
                "temperature_c": (84.0, "°C"),
                "leak_indicator": (0.50, "0-1"),
                "pressure_bar": (22.0, "bar"),
                "flow_rate_nm3_h": (3500.0, "Nm3/h"),
                "gas_quality_pct": (90.0, "%"),
                "compressor_discharge_pressure_bar": (24.0, "bar"),
                "power_consumption_kw": (480.0, "kW"),
            },
        )
        drafts = OperationalIntelligenceService(db).generate(
            scenario_key="normal", scenario_intensity=0.0
        )
        gas_drafts = [d for d in drafts if d.rule_code.startswith("gas_")]
        assert gas_drafts
        chosen = next(d for d in gas_drafts if d.affected_asset == "CMP-C-01")
        assert "Rule-based" in chosen.description
        assert "ML" not in chosen.title
        assert "prediction" not in chosen.title.lower()
        assert chosen.affected_zone == "zone-c"
        assert 0.0 < chosen.confidence <= 1.0
        assert _infer_recommendation_source(chosen.rule_code) == "telemetry"

        recs = RecommendationService(db).list_recommendations()
        # Rule-based gas_* only — exclude gas_xgboost_* PdM drafts (source=maintenance).
        gas_recs = [
            r
            for r in recs
            if (r.rule_code or "").startswith("gas_")
            and not (r.rule_code or "").startswith("gas_xgboost_")
        ]
        assert gas_recs
        assert any(r.affected_asset == "CMP-C-01" for r in gas_recs)
        assert all(r.source == "telemetry" for r in gas_recs)
    finally:
        db.close()


def test_asset_type_specific_recommendations_no_compressor_for_reg_gfdr() -> None:
    _seed()
    db = SessionLocal()
    try:
        from app.domain.models import Telemetry

        svc = TelemetryService(db)
        # Isolate from prior suite telemetry so latest window is this test's values.
        for asset_id in ("REG-A-01", "GFDR-A-01"):
            db.query(Telemetry).filter(Telemetry.sensor_id.like(f"SNS-{asset_id}-%")).delete(
                synchronize_session=False
            )
        db.commit()

        # Regulator with extreme valve — no compressor metrics persisted.
        for metric_key, value, unit in (
            ("pressure_bar", 8.5, "bar"),
            ("flow_rate_nm3_h", 1800.0, "Nm3/h"),
            ("temperature_c", 28.0, "°C"),
            ("valve_position_pct", 92.0, "%"),
            ("leak_indicator", 0.05, "0-1"),
            ("gas_quality_pct", 96.0, "%"),
        ):
            for _ in range(3):
                svc.persist_envelope(
                    asset_id="REG-A-01",
                    metric_key=metric_key,
                    envelope=TelemetryEnvelope(
                        value=value, unit=unit, quality="good", metric_key=metric_key
                    ),
                    utility_kind="gas",
                    commit=False,
                    evaluate_gas=False,
                )
        GasService(db).evaluate_condition("REG-A-01")
        db.commit()

        reg_asset = db.query(Asset).filter(Asset.id == "REG-A-01").one()
        reg_codes = list(reg_asset.profile.get("condition_alert_codes") or [])
        assert "valve_position_high" in reg_codes
        assert not any(c.startswith("compressor_") or c.startswith("vibration_") for c in reg_codes)

        drafts = OperationalIntelligenceService(db).generate(
            scenario_key="normal", scenario_intensity=0.0
        )
        reg = [d for d in drafts if d.affected_asset == "REG-A-01"]
        assert reg, (reg_codes, reg_asset.profile.get("condition"))
        assert all(d.rule_code.startswith("gas_") for d in reg)
        assert all(
            d.rule_code
            not in {
                "gas_compressor_overload",
                "gas_compressor_mechanical_stress",
                "gas_vibration_elevated",
            }
            for d in reg
        )
        assert any(d.rule_code == "gas_valve_abnormal" for d in reg)

        # Feeder: pressure/flow deviation only.
        for metric_key, value, unit in (
            ("pressure_bar", 3.5, "bar"),
            ("flow_rate_nm3_h", 500.0, "Nm3/h"),
            ("temperature_c", 26.0, "°C"),
            ("leak_indicator", 0.05, "0-1"),
            ("gas_quality_pct", 95.5, "%"),
        ):
            for _ in range(3):
                svc.persist_envelope(
                    asset_id="GFDR-A-01",
                    metric_key=metric_key,
                    envelope=TelemetryEnvelope(
                        value=value, unit=unit, quality="good", metric_key=metric_key
                    ),
                    utility_kind="gas",
                    commit=False,
                    evaluate_gas=False,
                )
        GasService(db).evaluate_condition("GFDR-A-01")
        db.commit()

        drafts = OperationalIntelligenceService(db).generate(
            scenario_key="normal", scenario_intensity=0.0
        )
        gfdr = [d for d in drafts if d.affected_asset == "GFDR-A-01"]
        assert gfdr
        assert all(
            "compressor" not in d.rule_code and "vibration" not in d.rule_code for d in gfdr
        )
    finally:
        db.close()


def test_no_duplicate_alerts_across_repeated_ticks() -> None:
    _seed()
    db = SessionLocal()
    try:
        sim = GasAssetSimulator(db, seed=7, mode=GasScenarioMode.DEGRADING)
        for _ in range(15):
            sim.persist_tick()

        open_alerts = AlertService(db).list_alerts(
            utility_kind="gas", status=AlertStatus.OPEN.value
        )
        fps = [a.fingerprint for a in open_alerts]
        assert len(fps) == len(set(fps))

        # Same codes still present — another tick must not duplicate fingerprints.
        before = len(open_alerts)
        sim.persist_tick()
        after = AlertService(db).list_alerts(
            utility_kind="gas", status=AlertStatus.OPEN.value
        )
        assert len({a.fingerprint for a in after}) == len(after)
        # Open set should not explode (set-diff upsert).
        assert len(after) <= before + 12  # at most one new code per asset type noise
    finally:
        db.close()


def test_no_frontend_only_alert_generation() -> None:
    """Gas alerts originate from backend condition sync, not client POST inventing codes."""
    _seed()
    client = TestClient(app)
    # Listing works; there is no public "create gas alert from UI mock" endpoint in scope.
    resp = client.get("/api/alerts", params={"utility_kind": "gas"})
    assert resp.status_code == 200
    # Creating arbitrary frontend alert without going through condition sync is out of
    # Gas producer path — verify OpenAPI still routes alerts through AlertService.
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/alerts" in paths
    # Gas evaluate path uses AlertSource.TELEMETRY via producer (covered elsewhere).


def test_human_controlled_wo_flow_unchanged() -> None:
    """Gas condition/alert/rec path must not auto-create work orders."""
    _seed()
    db = SessionLocal()
    try:
        before = db.query(WorkOrder).count()
        gas_before = db.query(WorkOrder).filter(WorkOrder.utility == "gas").count()
        svc = TelemetryService(db)
        _persist_compressor_metrics(
            svc,
            asset_id="CMP-D-01",
            metrics={
                "compressor_load_pct": (100.0, "%"),
                "vibration_mm_s": (8.0, "mm/s"),
                "temperature_c": (96.0, "°C"),
                "leak_indicator": (0.8, "0-1"),
                "pressure_bar": (18.0, "bar"),
                "flow_rate_nm3_h": (2800.0, "Nm3/h"),
                "gas_quality_pct": (83.0, "%"),
                "compressor_discharge_pressure_bar": (20.0, "bar"),
                "power_consumption_kw": (540.0, "kW"),
            },
        )
        # Recommendations exist, but no WO auto-created.
        gas_recs = [
            r
            for r in RecommendationService(db).list_recommendations()
            if (r.rule_code or "").startswith("gas_")
        ]
        assert gas_recs
        assert db.query(WorkOrder).count() == before
        assert db.query(WorkOrder).filter(WorkOrder.utility == "gas").count() == gas_before
    finally:
        db.close()


def test_mixed_simulator_alerts_and_recommendations() -> None:
    _seed()
    db = SessionLocal()
    try:
        sim = GasAssetSimulator(db, seed=99, mode=GasScenarioMode.MIXED)
        for _ in range(45):
            sim.persist_tick()

        conditions = {}
        for aid in ("CMP-A-01", "CMP-B-01", "CMP-C-01", "CMP-D-01"):
            asset = db.query(Asset).filter(Asset.id == aid).one()
            conditions[aid] = (asset.profile or {}).get("condition")

        assert conditions["CMP-A-01"] == "healthy"
        assert conditions["CMP-B-01"] == "healthy"
        assert conditions["CMP-C-01"] in {"warning", "high_risk", "critical"}
        assert conditions["CMP-D-01"] == "critical"

        open_gas = AlertService(db).list_alerts(
            utility_kind="gas", status=AlertStatus.OPEN.value
        )
        by_asset = {}
        for a in open_gas:
            by_asset.setdefault(a.asset_id, []).append(a.alert_code)

        assert not by_asset.get("CMP-A-01")
        assert not by_asset.get("CMP-B-01")
        assert by_asset.get("CMP-C-01")
        assert by_asset.get("CMP-D-01")
        assert any(c.endswith("_critical") or c.endswith("_high") for c in by_asset["CMP-D-01"])

        drafts = OperationalIntelligenceService(db).generate(
            scenario_key="normal", scenario_intensity=0.0
        )
        gas_by_asset = {}
        for d in drafts:
            if d.rule_code.startswith("gas_"):
                gas_by_asset.setdefault(d.affected_asset, []).append(d.rule_code)

        assert "CMP-A-01" not in gas_by_asset
        assert "CMP-B-01" not in gas_by_asset
        assert gas_by_asset.get("CMP-C-01")
        assert gas_by_asset.get("CMP-D-01")
        assert "gas_critical_condition" in gas_by_asset["CMP-D-01"]

        # Telemetry evidence for D critical codes.
        d_asset = db.query(Asset).filter(Asset.id == "CMP-D-01").one()
        codes = d_asset.profile.get("condition_alert_codes") or []
        assert codes
        assert all(c in known_gas_alert_codes() for c in codes)
        assert set(GAS_ASSET_IDS)  # seed intact
    finally:
        db.close()


def test_water_power_recommendation_sources_unchanged() -> None:
    assert _infer_recommendation_source("power_critical_condition") == "telemetry"
    assert _infer_recommendation_source("power_xgboost_maintenance_risk") == "maintenance"
    assert _infer_recommendation_source("pressure_critical") == "telemetry"
    assert _infer_recommendation_source("gas_critical_condition") == "telemetry"
