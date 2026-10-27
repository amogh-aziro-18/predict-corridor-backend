"""Phase 14.3C1 — Power transformer XGBoost predictive maintenance tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.db.seed import seed_all
from app.db.session import SessionLocal
from app.main import app
from app.ml.artifacts import PUMP_XGBOOST_PATH, TRANSFORMER_XGBOOST_PATH
from app.ml.power.constants import MODEL_VERSION_TRANSFORMER_XGBOOST
from app.ml.power.features import MODEL_NUMERIC_FEATURES, TransformerFeatureBuilder
from app.ml.power.labeling import (
    LABEL_COLUMN,
    add_transformer_labels,
    label_distribution,
)
from app.ml.power.xgboost_model import (
    load_artifact,
    predict_transformer_xgboost,
    train_transformer_xgboost,
)
from app.core.errors import ValidationAppError
from app.services.power_predictive_maintenance_service import (
    PowerPredictiveMaintenanceService,
)
from app.simulator.power.asset_simulator import PowerAssetSimulator
from app.simulator.power.scenarios import PowerScenarioMode


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _feature_frame(*, n: int = 48, mix: bool = True) -> pd.DataFrame:
    """Synthetic transformer feature panel with normal and stressed regimes."""
    rows: list[dict] = []
    now = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    for i in range(n):
        risky = mix and i % 2 == 0
        # Alternate assets across zones without encoding zone into the label path.
        asset = ["XFMR-A-01", "XFMR-B-01", "XFMR-C-01", "XFMR-D-01"][i % 4]
        zone = ["zone-a", "zone-b", "zone-c", "zone-d"][i % 4]
        oil = 88.0 if risky else 58.0
        load = 92.0 if risky else 55.0
        voltage = 120.0 if risky else 132.0
        pf = 0.84 if risky else 0.95
        rows.append(
            {
                "time": now + timedelta(minutes=5 * i),
                "asset_id": asset,
                "asset_name": f"Transformer {asset}",
                "utility_kind": "power",
                "zone": zone,
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
    return pd.DataFrame(rows)


def test_openapi_includes_power_maintenance_routes(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/predictions/power/maintenance/train" in paths
    assert "/api/predictions/power/maintenance" in paths
    assert "/api/predictions/power/maintenance/status" in paths
    assert "/api/predictions/power/maintenance/{asset_id}" in paths


def test_feature_builder_temporal_ordering_and_columns() -> None:
    raw = pd.DataFrame(
        [
            {
                "time": datetime(2026, 9, 28, 12, 10, tzinfo=timezone.utc),
                "asset_id": "XFMR-A-01",
                "asset_name": "A",
                "oil_temp_c": 60.0,
                "load_pct": 58.0,
                "voltage_kv": 132.0,
                "active_power_mw": 22.0,
                "power_factor": 0.95,
                "zone": "zone-a",
                "criticality": "high",
                "nominal_voltage_kv": 132.0,
                "utility_kind": "power",
            },
            {
                "time": datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc),
                "asset_id": "XFMR-A-01",
                "asset_name": "A",
                "oil_temp_c": 58.0,
                "load_pct": 55.0,
                "voltage_kv": 132.0,
                "active_power_mw": 21.0,
                "power_factor": 0.95,
                "zone": "zone-a",
                "criticality": "high",
                "nominal_voltage_kv": 132.0,
                "utility_kind": "power",
            },
            {
                "time": datetime(2026, 9, 28, 12, 5, tzinfo=timezone.utc),
                "asset_id": "XFMR-A-01",
                "asset_name": "A",
                "oil_temp_c": 59.0,
                "load_pct": 56.0,
                "voltage_kv": 131.8,
                "active_power_mw": 21.5,
                "power_factor": 0.94,
                "zone": "zone-a",
                "criticality": "high",
                "nominal_voltage_kv": 132.0,
                "utility_kind": "power",
            },
        ]
    )
    out = TransformerFeatureBuilder(window=3).build(raw)
    assert list(out["time"]) == sorted(out["time"].tolist())
    for col in MODEL_NUMERIC_FEATURES:
        assert col in out.columns
    # Slope should reflect chronological rise in oil temp.
    assert float(out.iloc[-1]["oil_temp_slope"]) >= 0.0


def test_labels_from_telemetry_not_zone_or_scenario() -> None:
    # Healthy metrics in zone-d must remain negative.
    healthy_d = _feature_frame(n=4, mix=False)
    healthy_d["zone"] = "zone-d"
    healthy_d["asset_id"] = "XFMR-D-01"
    labelled = add_transformer_labels(healthy_d)
    assert int(labelled[LABEL_COLUMN].sum()) == 0

    # Stressed metrics in zone-a must become positive.
    stressed_a = _feature_frame(n=4, mix=True)
    stressed_a = stressed_a[stressed_a.index % 2 == 0].copy()
    stressed_a["zone"] = "zone-a"
    stressed_a["asset_id"] = "XFMR-A-01"
    labelled_risk = add_transformer_labels(stressed_a)
    assert int(labelled_risk[LABEL_COLUMN].sum()) == len(labelled_risk)

    # Label frame must not depend on a scenario column.
    assert "scenario" not in labelled.columns
    assert "scenario_key" not in labelled.columns


def test_no_label_leakage_into_model_features() -> None:
    assert LABEL_COLUMN not in MODEL_NUMERIC_FEATURES
    assert "maintenance_label_score" not in MODEL_NUMERIC_FEATURES
    assert "condition" not in MODEL_NUMERIC_FEATURES
    assert "health" not in MODEL_NUMERIC_FEATURES
    assert "zone" not in MODEL_NUMERIC_FEATURES


def test_train_rejects_single_class(tmp_path: Path) -> None:
    frame = _feature_frame(n=40, mix=False)
    labelled = add_transformer_labels(frame)
    with pytest.raises(ValueError, match="both normal and maintenance-risk"):
        train_transformer_xgboost(labelled, artifact_path=tmp_path / "xfmr.pkl")


def test_train_predict_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "transformer_xgboost_test.pkl"
    labelled = add_transformer_labels(_feature_frame(n=64, mix=True))
    dist = label_distribution(labelled)
    assert dist["maintenance_risk"] > 0 and dist["normal"] > 0

    artifact = train_transformer_xgboost(labelled, artifact_path=path)
    assert artifact.model_version == MODEL_VERSION_TRANSFORMER_XGBOOST
    assert path.exists()
    assert "validation_metrics" in artifact.training_metadata
    assert artifact.training_metadata["assessment_kind"] == "predictive_maintenance_model"
    assert artifact.training_metadata["utility_kind"] == "power"

    loaded = load_artifact(path)
    assert loaded is not None
    probs, contribs = predict_transformer_xgboost(loaded, labelled.head(8))
    assert len(probs) == 8
    assert contribs.shape[1] == len(loaded.feature_names)
    assert all(0.0 <= float(p) <= 1.0 for p in probs)


def test_status_train_required_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "missing_transformer.pkl"
    monkeypatch.setattr(
        "app.services.power_predictive_maintenance_service.TRANSFORMER_XGBOOST_PATH",
        missing,
    )
    monkeypatch.setattr(
        "app.ml.power.xgboost_model.TRANSFORMER_XGBOOST_PATH",
        missing,
    )
    db = SessionLocal()
    try:
        status = PowerPredictiveMaintenanceService(db).status()
        assert status["inference"] == "train_required"
        assert status["artifact_exists"] is False
        assert status["assessment_kind"] == "predictive_maintenance_model"
    finally:
        db.close()


def test_service_insufficient_data_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    db = SessionLocal()
    try:
        service = PowerPredictiveMaintenanceService(db)

        def _tiny(**_kwargs):
            return add_transformer_labels(_feature_frame(n=10, mix=True)).drop(
                columns=[LABEL_COLUMN], errors="ignore"
            )

        # prepare_features returns tiny frame; train should reject.
        monkeypatch.setattr(service, "prepare_features", lambda **_k: _feature_frame(n=10, mix=True))
        with pytest.raises(ValidationAppError, match="Insufficient"):
            service.train()
    finally:
        db.close()


def test_directional_risk_from_simulator_and_train(tmp_path: Path) -> None:
    """Degrading transformers should outrank healthy ones after real training."""
    seed_all()
    db = SessionLocal()
    artifact_path = tmp_path / "transformer_live.pkl"
    try:
        sim = PowerAssetSimulator(db, seed=7, mode=PowerScenarioMode.MIXED)
        for _ in range(35):
            sim.persist_tick()

        service = PowerPredictiveMaintenanceService(db)
        features = service.prepare_features(limit=100_000)
        assert not features.empty
        assert set(features["asset_id"].unique()) <= {
            "XFMR-A-01",
            "XFMR-B-01",
            "XFMR-C-01",
            "XFMR-D-01",
        }
        # Must not include Water pumps.
        assert not any(str(a).startswith("PMP") for a in features["asset_id"].unique())

        labelled = add_transformer_labels(features)
        dist = label_distribution(labelled)
        if dist["maintenance_risk"] < 8 or dist["normal"] < 8:
            pytest.skip(
                f"Insufficient class balance from live sim for directional assert: {dist}"
            )

        artifact = train_transformer_xgboost(labelled, artifact_path=artifact_path)
        assert artifact_path.exists()

        latest = (
            features.sort_values(["asset_id", "time"], kind="mergesort")
            .groupby("asset_id", as_index=False)
            .tail(1)
            .reset_index(drop=True)
        )
        probs, _ = predict_transformer_xgboost(artifact, latest)
        by_asset = {
            str(aid): float(prob)
            for aid, prob in zip(latest["asset_id"], probs, strict=True)
        }
        healthy = max(by_asset.get("XFMR-A-01", 0.0), by_asset.get("XFMR-B-01", 0.0))
        degrading = max(by_asset.get("XFMR-C-01", 0.0), by_asset.get("XFMR-D-01", 0.0))
        assert degrading > healthy
    finally:
        db.close()


def test_water_pump_artifact_path_untouched() -> None:
    """Power training must not write into the Water pump artifact path."""
    assert TRANSFORMER_XGBOOST_PATH.name == "transformer_xgboost.pkl"
    assert PUMP_XGBOOST_PATH.name == "pump_xgboost.pkl"
    assert TRANSFORMER_XGBOOST_PATH != PUMP_XGBOOST_PATH


def test_ml_status_includes_transformer(client: TestClient) -> None:
    resp = client.get("/api/ml/status")
    assert resp.status_code == 200
    body = resp.json()
    names = {m["model"] for m in body.get("models", [])}
    assert "transformer_xgboost" in names
