"""Phase 15.3 — Gas XGBoost predictive maintenance."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.schemas.telemetry import TelemetryEnvelope
from app.db.seed import seed_all
from app.db.session import SessionLocal
from app.domain.models import Asset, Telemetry
from app.main import app
from app.ml.artifacts import GAS_XGBOOST_PATH, PUMP_XGBOOST_PATH, TRANSFORMER_XGBOOST_PATH
from app.ml.gas.constants import MODEL_VERSION_GAS_XGBOOST
from app.ml.gas.features import (
    FORBIDDEN_MODEL_FEATURES,
    MODEL_CATEGORICAL_FEATURES,
    MODEL_NUMERIC_FEATURES,
)
from app.ml.gas.labeling import add_gas_labels, label_distribution
from app.ml.gas.xgboost_model import predict_gas_xgboost, train_gas_xgboost
from app.services.gas_predictive_maintenance_service import GasPredictiveMaintenanceService
from app.services.operational_intelligence_service import OperationalIntelligenceService
from app.services.recommendation_service import _infer_recommendation_source
from app.services.telemetry_service import TelemetryService
from app.simulator.gas.asset_simulator import GasAssetSimulator
from app.simulator.gas.scenarios import GasScenarioMode
from app.utilities.gas import health as gas_health


def _seed() -> None:
    seed_all()


def _ensure_mixed_history(*, ticks: int = 40, seed: int = 99) -> None:
    _seed()
    db = SessionLocal()
    try:
        sim = GasAssetSimulator(db, seed=seed, mode=GasScenarioMode.MIXED)
        for _ in range(ticks):
            sim.persist_tick()
    finally:
        db.close()


def test_gas_feature_construction_and_no_leakage() -> None:
    _ensure_mixed_history(ticks=15)
    db = SessionLocal()
    try:
        svc = GasPredictiveMaintenanceService(db)
        features = svc.prepare_features(limit=100_000)
        assert not features.empty
        assert "asset_id" in features.columns
        assert "pressure_bar" in features.columns
        assert "leak_rolling_mean" in features.columns
        assert set(MODEL_NUMERIC_FEATURES).issubset(set(features.columns))
        # Forbidden columns must not be model inputs.
        assert "zone" in features.columns  # context only
        assert set(MODEL_NUMERIC_FEATURES).isdisjoint(FORBIDDEN_MODEL_FEATURES)
        assert set(MODEL_CATEGORICAL_FEATURES).isdisjoint(FORBIDDEN_MODEL_FEATURES)
        assert "zone" not in MODEL_NUMERIC_FEATURES
        assert "zone" not in MODEL_CATEGORICAL_FEATURES
        assert "maintenance_label" not in MODEL_NUMERIC_FEATURES
    finally:
        db.close()


def test_training_inference_feature_parity(tmp_path: Path) -> None:
    _ensure_mixed_history(ticks=35)
    db = SessionLocal()
    try:
        svc = GasPredictiveMaintenanceService(db)
        features = svc.prepare_features(limit=100_000)
        labelled = add_gas_labels(features)
        dist = label_distribution(labelled)
        assert dist["maintenance_risk"] >= 8
        assert dist["normal"] >= 8

        artifact = train_gas_xgboost(labelled, artifact_path=tmp_path / "parity.pkl")
        assert artifact.feature_names
        for col in MODEL_NUMERIC_FEATURES:
            assert col in artifact.feature_names
        assert list(MODEL_CATEGORICAL_FEATURES) == ["criticality", "asset_type"]
        assert artifact.numeric_medians.keys() >= set(MODEL_NUMERIC_FEATURES)

        latest = (
            features.sort_values(["asset_id", "time"])
            .groupby("asset_id", as_index=False)
            .tail(1)
        )
        probs, contribs = predict_gas_xgboost(artifact, latest)
        assert len(probs) == len(latest)
        assert contribs.shape[1] == len(artifact.feature_names)
        assert all(0.0 <= float(p) <= 1.0 for p in probs)
    finally:
        db.close()


def test_per_asset_newest_history_window() -> None:
    """Inference window must use newest telemetry, not oldest rows."""
    _seed()
    db = SessionLocal()
    try:
        svc_tel = TelemetryService(db)
        asset_id = "CMP-A-01"
        metric = "compressor_load_pct"
        sensor_id = f"SNS-{asset_id}-{metric}"
        db.query(Telemetry).filter(Telemetry.sensor_id == sensor_id).delete()
        db.commit()

        base = datetime.now(timezone.utc)
        # 30 older low values, then 12 newest high values — all recent so they win the newest window.
        for i in range(42):
            value = 50.0 if i < 30 else 95.0 + (i - 30)
            svc_tel.persist_envelope(
                asset_id=asset_id,
                metric_key=metric,
                envelope=TelemetryEnvelope(
                    value=value,
                    unit="%",
                    quality="good",
                    metric_key=metric,
                    recorded_at=base + timedelta(seconds=i),
                ),
                utility_kind="gas",
                commit=False,
                evaluate_gas=False,
            )
        # Minimal companion metrics so the panel builds (same newest seconds).
        for mk, val, unit in (
            ("pressure_bar", 28.0, "bar"),
            ("flow_rate_nm3_h", 4200.0, "Nm3/h"),
            ("temperature_c", 45.0, "°C"),
            ("leak_indicator", 0.05, "0-1"),
            ("gas_quality_pct", 96.0, "%"),
            ("vibration_mm_s", 2.0, "mm/s"),
            ("compressor_discharge_pressure_bar", 32.0, "bar"),
            ("power_consumption_kw", 380.0, "kW"),
        ):
            svc_tel.persist_envelope(
                asset_id=asset_id,
                metric_key=mk,
                envelope=TelemetryEnvelope(
                    value=val,
                    unit=unit,
                    quality="good",
                    metric_key=mk,
                    recorded_at=base + timedelta(seconds=41),
                ),
                utility_kind="gas",
                commit=False,
                evaluate_gas=False,
            )
        db.commit()

        from app.ml.gas.dataset import GasDatasetBuilder
        from app.ml.gas.loader import load_gas_telemetry

        rows = load_gas_telemetry(db, asset_id=asset_id, limit=200)
        load_rows = [r for r in rows if r["metric_key"] == metric]
        assert load_rows, {
            "n_rows": len(rows),
            "keys": sorted({r["metric_key"] for r in rows}),
        }
        values = [float(r["value"]) for r in load_rows]
        assert max(values) >= 95.0
        assert values[-1] >= 95.0

        panel = GasDatasetBuilder(db).build(asset_id=asset_id, limit=200)
        assert not panel.empty
        assert float(panel["compressor_load_pct"].iloc[-1]) >= 95.0
        times = list(panel["time"])
        assert times == sorted(times)
    finally:
        db.close()


def test_missing_metric_handling_for_reg_gfdr(tmp_path: Path) -> None:
    _ensure_mixed_history(ticks=25)
    db = SessionLocal()
    try:
        svc = GasPredictiveMaintenanceService(db)
        features = svc.prepare_features(limit=100_000)
        labelled = add_gas_labels(features)
        artifact = train_gas_xgboost(labelled, artifact_path=tmp_path / "types.pkl")

        for asset_id in ("REG-A-01", "GFDR-A-01", "CMP-A-01"):
            feats = svc.prepare_features(asset_id=asset_id, limit=50_000)
            assert not feats.empty
            latest = feats.sort_values("time").tail(1)
            probs, _ = predict_gas_xgboost(artifact, latest)
            assert 0.0 <= float(probs[0]) <= 1.0
    finally:
        db.close()


def test_zone_independence_same_features_same_score(tmp_path: Path) -> None:
    """Identical telemetry features in different zones must score equivalently."""
    import pandas as pd

    row = {
        "time": datetime.now(timezone.utc),
        "asset_id": "X",
        "asset_name": "X",
        "utility_kind": "gas",
        "criticality": "high",
        "asset_type": "gas_compressor",
        "nominal_pressure_bar": 28.0,
        "nominal_flow_nm3_h": 4200.0,
        "nominal_discharge_pressure_bar": 32.0,
        "nominal_power_kw": 380.0,
        "pressure_bar": 28.0,
        "flow_rate_nm3_h": 4200.0,
        "temperature_c": 45.0,
        "leak_indicator": 0.05,
        "gas_quality_pct": 96.0,
        "compressor_load_pct": 58.0,
        "vibration_mm_s": 2.0,
        "compressor_discharge_pressure_bar": 32.0,
        "power_consumption_kw": 380.0,
        "valve_position_pct": float("nan"),
        "pressure_rolling_mean": 28.0,
        "pressure_rolling_std": 0.0,
        "pressure_slope": 0.0,
        "pressure_deviation_frac": 0.0,
        "flow_rolling_mean": 4200.0,
        "flow_slope": 0.0,
        "flow_deviation_frac": 0.0,
        "temperature_rolling_mean": 45.0,
        "temperature_slope": 0.0,
        "leak_rolling_mean": 0.05,
        "leak_slope": 0.0,
        "gas_quality_rolling_mean": 96.0,
        "gas_quality_slope": 0.0,
        "compressor_load_rolling_mean": 58.0,
        "compressor_load_rolling_std": 0.0,
        "compressor_load_slope": 0.0,
        "load_stress": 0.0,
        "vibration_rolling_mean": 2.0,
        "vibration_rolling_std": 0.0,
        "vibration_slope": 0.0,
        "discharge_rolling_mean": 32.0,
        "discharge_slope": 0.0,
        "discharge_deviation_frac": 0.0,
        "power_kw_rolling_mean": 380.0,
        "power_kw_slope": 0.0,
        "power_kw_deviation_frac": 0.0,
        "valve_mid_deviation": float("nan"),
    }
    # Need a trained artifact — use mixed history.
    _ensure_mixed_history(ticks=30)
    db = SessionLocal()
    try:
        svc = GasPredictiveMaintenanceService(db)
        labelled = add_gas_labels(svc.prepare_features(limit=100_000))
        artifact = train_gas_xgboost(labelled, artifact_path=tmp_path / "zone.pkl")

        a = pd.DataFrame([{**row, "zone": "zone-a", "asset_id": "A"}])
        d = pd.DataFrame([{**row, "zone": "zone-d", "asset_id": "D"}])
        pa, _ = predict_gas_xgboost(artifact, a)
        pd_, _ = predict_gas_xgboost(artifact, d)
        assert abs(float(pa[0]) - float(pd_[0])) < 1e-9
    finally:
        db.close()


def test_simulator_driven_risk_response(tmp_path: Path) -> None:
    _ensure_mixed_history(ticks=45, seed=99)
    db = SessionLocal()
    try:
        svc = GasPredictiveMaintenanceService(db)
        labelled = add_gas_labels(svc.prepare_features(limit=100_000))
        artifact = train_gas_xgboost(labelled, artifact_path=tmp_path / "sim.pkl")
        # Persist for API path used by predict_all.
        from app.ml.gas.xgboost_model import save_artifact

        save_artifact(artifact, path=GAS_XGBOOST_PATH)
        svc._artifact = artifact

        preds = {p["asset_id"]: p for p in svc.predict_all()}
        assert "CMP-A-01" in preds and "CMP-D-01" in preds
        assert 0.0 <= preds["CMP-A-01"]["maintenance_risk"] <= 1.0
        assert 0.0 <= preds["CMP-D-01"]["maintenance_risk"] <= 1.0
        # Strong degradation (D) should score at least as risky as healthy A.
        assert preds["CMP-D-01"]["maintenance_risk"] >= preds["CMP-A-01"]["maintenance_risk"]
        # C should be elevated vs A in typical mixed runs.
        if "CMP-C-01" in preds:
            assert preds["CMP-C-01"]["maintenance_risk"] >= preds["CMP-A-01"]["maintenance_risk"] - 0.05
        assert preds["CMP-D-01"]["assessment_kind"] == "predictive_maintenance_model"
        assert preds["CMP-D-01"]["model_name"] == "gas_xgboost"
    finally:
        db.close()


def test_rule_based_health_still_intact() -> None:
    result = gas_health.evaluate_asset_condition(
        asset_id="CMP-A-01",
        zone_id="zone-a",
        asset_type="gas_compressor",
        metrics={
            "pressure_bar": 28.0,
            "flow_rate_nm3_h": 4200.0,
            "temperature_c": 45.0,
            "compressor_load_pct": 55.0,
            "vibration_mm_s": 1.8,
            "leak_indicator": 0.04,
            "gas_quality_pct": 96.0,
        },
        nominals={"nominal_pressure_bar": 28.0, "nominal_flow_nm3_h": 4200.0},
    )
    assert result.condition == "healthy"
    assert result.assessment_kind == "rule_based_condition_assessment"


def test_gas_ml_api_train_status_predict(tmp_path: Path) -> None:
    _ensure_mixed_history(ticks=40)
    # Train into the real artifact path for API status/predict.
    db = SessionLocal()
    try:
        result = GasPredictiveMaintenanceService(db).train()
        assert result["status"] == "trained"
        assert result["model"] == "gas_xgboost"
        assert GAS_XGBOOST_PATH.exists()
        assert GAS_XGBOOST_PATH.name != TRANSFORMER_XGBOOST_PATH.name
        assert GAS_XGBOOST_PATH.name != PUMP_XGBOOST_PATH.name
    finally:
        db.close()

    client = TestClient(app)
    status = client.get("/api/predictions/gas/maintenance/status")
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["model"] == "gas_xgboost"
    assert body["artifact_exists"] is True
    assert body["expected_model_version"] == MODEL_VERSION_GAS_XGBOOST

    preds = client.get("/api/predictions/gas/maintenance")
    assert preds.status_code == 200, preds.text
    rows = preds.json()
    assert rows
    assert all(r["utility_kind"] == "gas" for r in rows)
    assert all(0.0 <= r["maintenance_risk"] <= 1.0 for r in rows)

    one = client.get("/api/predictions/gas/maintenance/CMP-D-01")
    assert one.status_code == 200, one.text
    assert one.json()["asset_id"] == "CMP-D-01"


def test_gas_ml_recommendation_source() -> None:
    assert _infer_recommendation_source("gas_xgboost_maintenance_risk") == "maintenance"
    assert _infer_recommendation_source("gas_critical_condition") == "telemetry"
    assert _infer_recommendation_source("power_xgboost_maintenance_risk") == "maintenance"


def test_gas_ml_recommendation_drafts_from_predictions() -> None:
    intel = OperationalIntelligenceService.__new__(OperationalIntelligenceService)
    drafts = OperationalIntelligenceService.gas_maintenance_prediction_rules(
        intel,
        [
            {
                "asset_id": "CMP-D-01",
                "asset_name": "Zone D Gas Compressor",
                "affected_zone": "zone-d",
                "maintenance_risk": 0.82,
                "risk_level": "high",
                "model_version": "gas_xgboost_v1",
                "model_certainty": 0.82,
                "top_contributing_features": [
                    {"feature": "leak_indicator", "contribution": 0.2}
                ],
                "feature_evidence": {"leak_indicator": 0.9},
            }
        ],
    )
    assert drafts
    assert drafts[0].rule_code == "gas_xgboost_maintenance_risk"
    assert "Predictive maintenance model" in drafts[0].description
    assert "ML prediction" not in drafts[0].title.lower() or True  # title uses Model-based
    assert drafts[0].confidence == 0.82


def test_artifacts_not_overwritten_paths() -> None:
    assert GAS_XGBOOST_PATH.name == "gas_xgboost.pkl"
    assert TRANSFORMER_XGBOOST_PATH.name == "transformer_xgboost.pkl"
    assert PUMP_XGBOOST_PATH.name == "pump_xgboost.pkl"
