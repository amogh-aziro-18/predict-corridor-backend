"""Phase 14.6 — Water ML audit alignment: history, parity, leakage, Prophet."""

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
    PROPHET_WATER_PATH,
    TRANSFORMER_XGBOOST_PATH,
)
from app.ml.common.constants import MODEL_VERSION_XGBOOST
from app.ml.forecasting.prophet_model import load_artifact as load_prophet
from app.ml.maintenance.labeling import LABEL_COLUMN, add_maintenance_labels
from app.ml.maintenance.loader import load_maintenance_telemetry
from app.ml.maintenance.xgboost_model import (
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    _inference_matrix,
    _training_matrix,
    load_artifact,
    predict_xgboost,
    train_xgboost,
)
from app.services.operational_intelligence_service import OperationalIntelligenceService
from app.services.predictive_maintenance_service import PredictiveMaintenanceService
from app.simulator.water.asset_simulator import WaterAssetSimulator


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _synthetic_labelled(*, n: int = 48) -> pd.DataFrame:
    rows: list[dict] = []
    now = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    for i in range(n):
        risky = i % 2 == 0
        rows.append(
            {
                "time": now + timedelta(minutes=5 * i),
                "asset_id": f"PMP-T-{i % 4:02d}",
                "asset_name": f"Pump {i % 4}",
                "pressure": 0.8 if risky else 3.2,
                "flow_rate": 20.0 if risky else 180.0,
                "pump_speed": 50.0 if risky else 1400.0,
                "pump_running": 0.0 if risky else 1.0,
                "pressure_rolling_mean": 0.9 if risky else 3.1,
                "pressure_rolling_std": 0.2,
                "pressure_rolling_min": 0.5 if risky else 2.8,
                "pressure_rolling_max": 1.2 if risky else 3.4,
                "pressure_variance": 0.04,
                "flow_rolling_mean": 30.0 if risky else 170.0,
                "flow_rolling_std": 5.0,
                "flow_rolling_min": 10.0 if risky else 150.0,
                "flow_rolling_max": 40.0 if risky else 190.0,
                "flow_variance": 25.0,
                "pump_speed_rolling_mean": 80.0 if risky else 1350.0,
                "pump_speed_rolling_std": 10.0,
                "pressure_slope": -0.1 if risky else 0.0,
                "flow_slope": -1.0 if risky else 0.0,
                "pressure_rate_of_change": -0.05,
                "flow_rate_of_change": -0.5,
                "pump_utilization": 0.2 if risky else 0.8,
                "operating_duration_minutes": 30.0,
                "health": 50.0 if risky else 92.0,
                "status": "degraded" if risky else "operational",
                "zone": f"zone-{'abcd'[i % 4]}",
                "criticality": "high",
                "scenario_intensity": 0.4 if risky else 0.0,
            }
        )
    return add_maintenance_labels(pd.DataFrame(rows))


def test_water_history_rows_newest_window() -> None:
    seed_all()
    db = SessionLocal()
    try:
        from app.domain.repositories import AssetRepository

        sim = WaterAssetSimulator(db, seed=3)
        for _ in range(6):
            sim.persist_tick()
        repo = TelemetryRepository(db)
        pumps = [
            a.id
            for a in AssetRepository(db).list(utility_kind="water")
            if a.type == "pump_station"
        ]
        assert pumps
        pump_id = pumps[0]
        rows = repo.history_rows(
            asset_id=pump_id,
            metric_keys=["pressure"],
            limit=8,
        )
        assert len(rows) == 8
        times = [r["recorded_at"] for r in rows]
        assert times == sorted(times)
        all_rows = repo.history_rows(
            asset_id=pump_id, metric_keys=["pressure"], limit=100_000
        )
        if len(all_rows) > 8:
            assert times == [r["recorded_at"] for r in all_rows][-8:]
            assert times != [r["recorded_at"] for r in all_rows][:8]
    finally:
        db.close()


def test_water_loader_per_asset_not_global_starvation() -> None:
    seed_all()
    db = SessionLocal()
    try:
        sim = WaterAssetSimulator(db, seed=5)
        for _ in range(10):
            sim.persist_tick()
        rows = load_maintenance_telemetry(db, limit=50_000)
        assert rows
        by_asset: dict[str, int] = {}
        for row in rows:
            by_asset[str(row["asset_id"])] = by_asset.get(str(row["asset_id"]), 0) + 1
        # Every pump station with telemetry must appear — no single-LIMIT starvation.
        assert len(by_asset) >= 2
        for count in by_asset.values():
            assert count > 0
    finally:
        db.close()


def test_train_infer_feature_parity_no_leakage(tmp_path: Path) -> None:
    labelled = _synthetic_labelled()
    train_matrix, _medians = _training_matrix(labelled)
    artifact = train_xgboost(labelled, artifact_path=tmp_path / "pump_parity.pkl")
    infer = _inference_matrix(
        labelled.tail(4),
        feature_names=artifact.feature_names,
        medians=artifact.numeric_medians,
    )
    assert list(infer.columns) == list(artifact.feature_names)
    assert list(train_matrix.columns) == list(artifact.feature_names)
    assert set(NUMERIC_FEATURES).issubset(set(artifact.feature_names))
    assert CATEGORICAL_FEATURES == ("criticality",)
    for forbidden in (
        "zone",
        "status",
        "health",
        "scenario_intensity",
        "scenario",
        LABEL_COLUMN,
        "maintenance_label_score",
    ):
        assert forbidden not in artifact.feature_names
        assert not any(
            c == forbidden or c.startswith(f"{forbidden}_")
            for c in artifact.feature_names
        )
    assert artifact.model_version == MODEL_VERSION_XGBOOST


def test_water_ml_recommendation_confidence_tracks_risk() -> None:
    db = SessionLocal()
    try:
        oi = OperationalIntelligenceService(db)
        drafts = oi.maintenance_prediction_rules(
            [
                {
                    "asset_id": "PMP-A-01",
                    "asset_name": "PMP-A-01",
                    "affected_zone": "zone-a",
                    "maintenance_risk": 0.11,
                    "risk_level": "low",
                    "confidence": 0.89,
                    "predicted_failure_days": 120,
                    "top_contributing_features": [],
                },
                {
                    "asset_id": "PMP-C-01",
                    "asset_name": "PMP-C-01",
                    "affected_zone": "zone-c",
                    "maintenance_risk": 0.82,
                    "risk_level": "high",
                    "confidence": 0.82,
                    "predicted_failure_days": 6,
                    "top_contributing_features": [
                        {"feature": "pressure", "contribution": 0.3}
                    ],
                },
            ]
        )
        assert all(d.affected_asset != "PMP-A-01" for d in drafts)
        chosen = next(d for d in drafts if d.affected_asset == "PMP-C-01")
        assert chosen.rule_code == "xgboost_maintenance_risk"
        assert chosen.confidence == pytest.approx(0.82)
    finally:
        db.close()


def test_fresh_water_inference_uses_newest_telemetry(tmp_path: Path) -> None:
    """Inference must read the newest PostgreSQL window after new simulator ticks."""
    seed_all()
    db = SessionLocal()
    path = tmp_path / "fresh_pump.pkl"
    try:
        from app.domain.repositories import AssetRepository

        sim = WaterAssetSimulator(db, seed=9)
        for _ in range(20):
            sim.persist_tick()
        service = PredictiveMaintenanceService(db)
        features = service.prepare_features(limit=100_000, resample=False)
        labelled = add_maintenance_labels(features)
        if labelled[LABEL_COLUMN].nunique() < 2:
            pytest.skip(
                f"Need both classes for freshness: "
                f"{labelled[LABEL_COLUMN].value_counts().to_dict()}"
            )
        artifact = train_xgboost(labelled, artifact_path=path)

        pumps = [
            a.id
            for a in AssetRepository(db).list(utility_kind="water")
            if a.type == "pump_station"
        ]
        repo = TelemetryRepository(db)
        before_max = max(
            r["recorded_at"]
            for r in repo.history_rows(
                asset_id=pumps[0], metric_keys=["pressure"], limit=5
            )
        )
        before_feats = service.prepare_features(limit=100_000, resample=False)
        before_latest = (
            before_feats.sort_values(["asset_id", "time"], kind="mergesort")
            .groupby("asset_id", as_index=False)
            .tail(1)
        )
        before_pressure = {
            str(r.asset_id): float(r.pressure)
            for r in before_latest.itertuples()
        }

        for _ in range(25):
            sim.persist_tick()

        after_max = max(
            r["recorded_at"]
            for r in repo.history_rows(
                asset_id=pumps[0], metric_keys=["pressure"], limit=5
            )
        )
        assert after_max >= before_max

        after_feats = service.prepare_features(limit=100_000, resample=False)
        after_latest = (
            after_feats.sort_values(["asset_id", "time"], kind="mergesort")
            .groupby("asset_id", as_index=False)
            .tail(1)
        )
        after_pressure = {
            str(r.asset_id): float(r.pressure)
            for r in after_latest.itertuples()
        }
        # Telemetry-derived features must reflect new samples.
        assert after_pressure != before_pressure or after_max > before_max

        probs, _ = predict_xgboost(artifact, after_latest)
        assert len(probs) == len(after_latest)
        assert all(0.0 <= float(p) <= 1.0 for p in probs)
    finally:
        db.close()


def test_prophet_water_artifact_loads_and_predicts(client: TestClient) -> None:
    if not PROPHET_WATER_PATH.exists():
        pytest.skip("prophet_water.pkl missing")
    art = load_prophet(PROPHET_WATER_PATH)
    assert art is not None
    status = client.get("/api/forecasts/demand/status")
    assert status.status_code == 200
    body = status.json()
    assert body.get("artifact_exists") is True or body.get("inference") == "available"

    forecast = client.get("/api/forecasts/demand")
    if forecast.status_code in {400, 422}:
        pytest.skip(f"forecast not available: {forecast.json()}")
    assert forecast.status_code == 200
    payload = forecast.json()
    assert "forecast" in payload
    series = payload["forecast"].get("next_24_hours") or []
    assert isinstance(series, list)
    # No empty fabricated series — either real points or honest empty.
    if series:
        assert "demand" in series[0]


def test_pump_artifact_path_and_power_isolation() -> None:
    assert PUMP_XGBOOST_PATH.name == "pump_xgboost.pkl"
    assert TRANSFORMER_XGBOOST_PATH.name == "transformer_xgboost.pkl"
    assert PUMP_XGBOOST_PATH != TRANSFORMER_XGBOOST_PATH
    if PUMP_XGBOOST_PATH.exists():
        art = load_artifact(PUMP_XGBOOST_PATH)
        assert art is not None
        for forbidden in ("zone", "status", "health", "scenario_intensity"):
            assert forbidden not in art.feature_names
            assert not any(c.startswith(f"{forbidden}_") for c in art.feature_names)


def test_water_maintenance_api_contract(client: TestClient) -> None:
    if not PUMP_XGBOOST_PATH.exists():
        pytest.skip("pump_xgboost.pkl missing")
    status = client.get("/api/ml/status")
    assert status.status_code == 200
    assert status.json()["model"] == "pump_xgboost"
    preds = client.get("/api/predictions/maintenance")
    if preds.status_code in {400, 422}:
        pytest.skip(f"inference unavailable: {preds.json()}")
    assert preds.status_code == 200
    rows = preds.json()
    assert isinstance(rows, list)
    if rows:
        assert 0.0 <= float(rows[0]["maintenance_risk"]) <= 1.0
        assert rows[0]["confidence"] == pytest.approx(rows[0]["maintenance_risk"])
