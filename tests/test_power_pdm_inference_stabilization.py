"""Phase 14.5 — Power PdM inference freshness, feature parity, no leakage."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.db.seed import seed_all
from app.db.session import SessionLocal
from app.domain.repositories.telemetry_repository import TelemetryRepository
from app.main import app
from app.ml.artifacts import (
    PUMP_XGBOOST_PATH,
    PROPHET_POWER_LOAD_PATH,
    PROPHET_WATER_PATH,
    TRANSFORMER_XGBOOST_PATH,
)
from app.ml.power.features import MODEL_CATEGORICAL_FEATURES, MODEL_NUMERIC_FEATURES
from app.ml.power.xgboost_model import (
    _inference_matrix,
    _training_matrix,
    predict_transformer_xgboost,
    train_transformer_xgboost,
)
from app.ml.power.labeling import LABEL_COLUMN, add_transformer_labels
from app.services.operational_intelligence_service import OperationalIntelligenceService
from app.services.power_predictive_maintenance_service import (
    PowerPredictiveMaintenanceService,
)
from app.simulator.power.asset_simulator import PowerAssetSimulator
from app.simulator.power.scenarios import PowerScenarioMode


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _synthetic_labelled(*, n: int = 64) -> pd.DataFrame:
    rows: list[dict] = []
    now = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    for i in range(n):
        risky = i % 2 == 0
        oil = 88.0 if risky else 58.0
        load = 92.0 if risky else 55.0
        voltage = 120.0 if risky else 132.0
        pf = 0.84 if risky else 0.95
        rows.append(
            {
                "time": now + timedelta(minutes=5 * i),
                "asset_id": ["XFMR-A-01", "XFMR-C-01"][i % 2],
                "asset_name": "T",
                "utility_kind": "power",
                "zone": ["zone-a", "zone-c"][i % 2],
                "criticality": "high",
                "nominal_voltage_kv": 132.0,
                "oil_temp_c": oil,
                "load_pct": load,
                "voltage_kv": voltage,
                "active_power_mw": 35.0 if risky else 22.0,
                "power_factor": pf,
                "oil_temp_rolling_mean": oil - 1.0,
                "oil_temp_rolling_std": 1.5,
                "oil_temp_slope": 0.4 if risky else 0.0,
                "load_rolling_mean": load - 1.0,
                "load_rolling_std": 2.0,
                "load_slope": 0.5 if risky else 0.0,
                "voltage_rolling_mean": voltage + 0.5,
                "voltage_rolling_std": 0.4,
                "voltage_slope": -0.2 if risky else 0.0,
                "power_factor_rolling_mean": pf + 0.01,
                "power_factor_slope": -0.002 if risky else 0.0,
                "active_power_rolling_mean": 34.0 if risky else 22.0,
                "active_power_slope": 0.1 if risky else 0.0,
                "voltage_deviation_kv": 132.0 - voltage,
                "load_stress": max(0.0, load - 70.0),
                "oil_temp_delta": 1.0 if risky else 0.0,
                "power_factor_deviation": 0.95 - pf,
            }
        )
    return add_transformer_labels(pd.DataFrame(rows))


def test_history_rows_returns_newest_window_not_oldest() -> None:
    """ASC+LIMIT alone would freeze ML on the oldest telemetry once the table grows."""
    seed_all()
    db = SessionLocal()
    try:
        sim = PowerAssetSimulator(db, seed=11, mode=PowerScenarioMode.MIXED)
        for _ in range(8):
            sim.persist_tick()

        repo = TelemetryRepository(db)
        # Small limit forces the newest-window CTE to matter.
        rows = repo.history_rows(
            asset_id="XFMR-C-01",
            metric_keys=["oil_temp_c"],
            limit=10,
        )
        assert len(rows) == 10
        times = [r["recorded_at"] for r in rows]
        assert times == sorted(times)

        all_c = repo.history_rows(
            asset_id="XFMR-C-01",
            metric_keys=["oil_temp_c"],
            limit=100_000,
        )
        assert len(all_c) >= 10
        newest_expected = [r["recorded_at"] for r in all_c][-10:]
        assert times == newest_expected
        # Must not equal the oldest 10 rows once history exceeds the limit.
        oldest = [r["recorded_at"] for r in all_c][:10]
        if len(all_c) > 10:
            assert times != oldest
    finally:
        db.close()


def test_train_infer_feature_schema_parity(tmp_path: Path) -> None:
    labelled = _synthetic_labelled()
    train_matrix, medians = _training_matrix(labelled)
    artifact = train_transformer_xgboost(labelled, artifact_path=tmp_path / "parity.pkl")

    infer_matrix = _inference_matrix(
        labelled.tail(4),
        feature_names=artifact.feature_names,
        medians=artifact.numeric_medians,
    )
    assert list(infer_matrix.columns) == list(artifact.feature_names)
    assert set(MODEL_NUMERIC_FEATURES).issubset(set(artifact.feature_names))
    # Zone / scenario / label must never appear as model inputs.
    for forbidden in ("zone", "scenario", "scenario_key", LABEL_COLUMN, "health", "condition"):
        assert forbidden not in artifact.feature_names
        assert not any(c.startswith(f"{forbidden}_") for c in artifact.feature_names)
    # Categorical encoding is criticality-only.
    for col in artifact.feature_names:
        if col not in MODEL_NUMERIC_FEATURES:
            assert col.startswith("criticality_")
    # Deterministic column order: training matrix columns == artifact feature_names.
    assert list(train_matrix.columns) == list(artifact.feature_names)
    assert list(MODEL_CATEGORICAL_FEATURES) == ["criticality"]
    assert medians.keys() >= set(MODEL_NUMERIC_FEATURES)


def test_fresh_inference_changes_with_new_telemetry(tmp_path: Path) -> None:
    """Predictions must recompute from PostgreSQL telemetry — not a static cache."""
    seed_all()
    db = SessionLocal()
    artifact_path = tmp_path / "fresh.pkl"
    try:
        sim = PowerAssetSimulator(db, seed=19, mode=PowerScenarioMode.MIXED)
        for _ in range(25):
            sim.persist_tick()

        service = PowerPredictiveMaintenanceService(db)
        features = service.prepare_features(limit=100_000)
        labelled = add_transformer_labels(features)
        if labelled[LABEL_COLUMN].nunique() < 2:
            pytest.skip("Need both classes from mixed simulator before freshness assert")

        artifact = train_transformer_xgboost(labelled, artifact_path=artifact_path)

        def _latest_probs() -> dict[str, float]:
            feats = service.prepare_features(limit=100_000)
            latest = (
                feats.sort_values(["asset_id", "time"], kind="mergesort")
                .groupby("asset_id", as_index=False)
                .tail(1)
                .reset_index(drop=True)
            )
            probs, _ = predict_transformer_xgboost(artifact, latest)
            return {
                str(aid): float(p)
                for aid, p in zip(latest["asset_id"], probs, strict=True)
            }

        before = _latest_probs()
        # Drive further C/D stress into PostgreSQL.
        for _ in range(40):
            sim.persist_tick()
        after = _latest_probs()

        assert set(before) == set(after)
        # At least one transformer probability must move after new telemetry.
        deltas = [abs(after[a] - before[a]) for a in before]
        assert max(deltas) > 1e-6, f"stale inference: before={before} after={after}"
    finally:
        db.close()


def test_power_ml_recommendation_confidence_tracks_risk_not_certainty() -> None:
    db = SessionLocal()
    try:
        oi = OperationalIntelligenceService(db)
        drafts = oi.power_maintenance_prediction_rules(
            [
                {
                    "asset_id": "XFMR-C-01",
                    "asset_name": "XFMR-C-01",
                    "affected_zone": "zone-c",
                    "maintenance_risk": 0.12,
                    "risk_level": "low",
                    "model_certainty": 0.88,
                    "model_version": "transformer_xgboost_v1",
                    "feature_evidence": {},
                    "top_contributing_features": [],
                },
                {
                    "asset_id": "XFMR-D-01",
                    "asset_name": "XFMR-D-01",
                    "affected_zone": "zone-d",
                    "maintenance_risk": 0.93,
                    "risk_level": "critical",
                    "model_certainty": 0.93,
                    "model_version": "transformer_xgboost_v1",
                    "feature_evidence": {"oil_temp_c": 95.0},
                    "top_contributing_features": [
                        {"feature": "oil_temp_c", "contribution": 0.4}
                    ],
                },
            ]
        )
        # Below threshold → no draft (evidence-based, not zone-based).
        assert all(d.affected_asset != "XFMR-C-01" for d in drafts)
        d_draft = next(d for d in drafts if d.affected_asset == "XFMR-D-01")
        assert d_draft.rule_code == "power_xgboost_maintenance_risk"
        assert d_draft.confidence == pytest.approx(0.93)
        assert d_draft.confidence != pytest.approx(0.5)  # not a hardcoded mock
    finally:
        db.close()


def test_prediction_api_uses_transformer_artifact(client: TestClient) -> None:
    if not TRANSFORMER_XGBOOST_PATH.exists():
        pytest.skip("transformer_xgboost.pkl missing — train before API smoke")
    status = client.get("/api/predictions/power/maintenance/status")
    assert status.status_code == 200
    body = status.json()
    assert body["model"] == "transformer_xgboost"
    assert body["artifact_exists"] is True
    assert body["assessment_kind"] == "predictive_maintenance_model"
    assert "pump" not in (body.get("artifact_path") or "").lower()

    preds = client.get("/api/predictions/power/maintenance")
    if preds.status_code == 400:
        pytest.skip(f"inference not available yet: {preds.json()}")
    assert preds.status_code == 200
    rows = preds.json()
    assert isinstance(rows, list)
    if rows:
        row = rows[0]
        assert "maintenance_risk" in row
        assert "risk_level" in row
        assert row.get("assessment_kind") == "predictive_maintenance_model"
        assert row.get("model_name") == "transformer_xgboost"
        assert 0.0 <= float(row["maintenance_risk"]) <= 1.0


def test_water_ml_artifacts_untouched() -> None:
    assert TRANSFORMER_XGBOOST_PATH.name == "transformer_xgboost.pkl"
    assert PUMP_XGBOOST_PATH.name == "pump_xgboost.pkl"
    assert PROPHET_POWER_LOAD_PATH.name == "prophet_power_load.pkl"
    assert PROPHET_WATER_PATH.name == "prophet_water.pkl"
    assert TRANSFORMER_XGBOOST_PATH != PUMP_XGBOOST_PATH
    assert PROPHET_POWER_LOAD_PATH != PROPHET_WATER_PATH
    # Paths must remain distinct files on disk (Water not overwritten by Power).
    if PUMP_XGBOOST_PATH.exists() and TRANSFORMER_XGBOOST_PATH.exists():
        assert PUMP_XGBOOST_PATH.read_bytes() != TRANSFORMER_XGBOOST_PATH.read_bytes()
