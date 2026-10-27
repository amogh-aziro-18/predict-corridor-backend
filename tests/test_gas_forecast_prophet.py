"""Phase 15.4 — Gas Prophet demand forecasting tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.core.errors import ValidationAppError
from app.db.seed import seed_all
from app.db.session import SessionLocal
from app.main import app
from app.ml.artifacts import (
    GAS_XGBOOST_PATH,
    PROPHET_GAS_DEMAND_PATH,
    PROPHET_POWER_LOAD_PATH,
    PROPHET_WATER_PATH,
)
from app.ml.gas.forecast_constants import (
    GAS_DEMAND_METRIC_KEY,
    MODEL_VERSION_GAS_PROPHET,
)
from app.ml.gas.forecast_dataset import GasDemandDatasetBuilder
from app.ml.gas.forecast_loader import resolve_gas_demand_asset_ids
from app.ml.gas.prophet_model import (
    load_artifact,
    predict_gas_prophet,
    to_prophet_frame,
    train_gas_prophet,
)
from app.services.gas_forecast_service import GasForecastService
from app.simulator.gas.asset_simulator import GasAssetSimulator
from app.simulator.gas.scenarios import GasScenarioMode


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _synthetic_demand(hours: int = 36, freq: str = "1min") -> pd.DataFrame:
    end = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    index = pd.date_range(end=end, periods=hours * 60, freq=freq, tz="UTC")
    values = [3800.0 + (i % 60) * 2.5 + (i % 11) * 1.2 for i in range(len(index))]
    return pd.DataFrame({"time": index, "demand": values})


def test_openapi_includes_gas_forecast_routes(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/predictions/gas/forecast" in paths
    assert "/api/predictions/gas/forecast/train" in paths
    assert "/api/predictions/gas/forecast/status" in paths


def test_target_metric_is_flow_rate() -> None:
    assert GAS_DEMAND_METRIC_KEY == "flow_rate_nm3_h"


def test_to_prophet_frame_requires_columns() -> None:
    with pytest.raises(ValueError, match="time, demand"):
        to_prophet_frame(pd.DataFrame({"x": [1], "y": [2]}))


def test_to_prophet_frame_orders_and_dedupes() -> None:
    ts = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    frame = pd.DataFrame(
        {
            "time": [ts + timedelta(minutes=1), ts, ts],
            "demand": [120.0, 100.0, 140.0],
        }
    )
    out = to_prophet_frame(frame)
    assert list(out["ds"]) == sorted(out["ds"].tolist())
    assert len(out) == 2
    first = out.iloc[0]
    assert abs(float(first["y"]) - 120.0) < 1e-6


def test_train_rejects_insufficient_history(tmp_path: Path) -> None:
    series = _synthetic_demand(hours=1).iloc[:5]
    with pytest.raises(ValueError, match="Insufficient Gas telemetry"):
        train_gas_prophet(series, artifact_path=tmp_path / "g.pkl")


def test_train_predict_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "prophet_gas_demand_test.pkl"
    series = _synthetic_demand(hours=12)
    artifact = train_gas_prophet(
        series,
        demand_source={
            "utility_kind": "gas",
            "metric_key": GAS_DEMAND_METRIC_KEY,
            "aggregation": "sum",
        },
        resample_freq="1min",
        artifact_path=path,
    )
    assert path.exists()
    assert artifact.model_version == MODEL_VERSION_GAS_PROPHET
    assert artifact.training_metadata["utility_kind"] == "gas"
    assert artifact.training_metadata["target_metric"] == GAS_DEMAND_METRIC_KEY
    assert "validation_metrics" in artifact.training_metadata

    loaded = load_artifact(path)
    assert loaded is not None
    forecast = predict_gas_prophet(loaded, hours=6)
    assert not forecast.empty
    assert {"ds", "yhat", "yhat_lower", "yhat_upper"}.issubset(forecast.columns)
    assert (forecast["yhat"] >= 0).all()
    hist_end = pd.Timestamp(loaded.training_window["end"]).tz_localize(None)
    assert (forecast["ds"] > hist_end).all()


def test_training_inference_series_compatibility(tmp_path: Path) -> None:
    """Same prepared series shape works for train and subsequent predict framing."""
    series = _synthetic_demand(hours=8)
    frame_train = to_prophet_frame(series)
    artifact = train_gas_prophet(series, resample_freq="1min", artifact_path=tmp_path / "p.pkl")
    frame_again = to_prophet_frame(series)
    assert list(frame_train.columns) == list(frame_again.columns) == ["ds", "y"]
    assert len(frame_train) == len(frame_again)
    forecast = predict_gas_prophet(artifact, periods=6)
    assert len(forecast) >= 1


def test_chronological_metrics_present_for_long_series(tmp_path: Path) -> None:
    path = tmp_path / "prophet_gas_metrics.pkl"
    artifact = train_gas_prophet(
        _synthetic_demand(hours=24),
        resample_freq="1min",
        artifact_path=path,
    )
    metrics = artifact.training_metadata.get("validation_metrics") or {}
    note = artifact.training_metadata.get("validation_note")
    assert note == "chronological holdout"
    assert "mae" in metrics and "rmse" in metrics
    assert metrics["holdout_rows"] >= 5


def test_status_train_required_when_missing(tmp_path: Path) -> None:
    missing = tmp_path / "missing_gas_prophet.pkl"
    service = GasForecastService(MagicMock())
    with patch.object(GasForecastService, "_artifact_path_for_zone", return_value=missing):
        with patch("app.services.gas_forecast_service.load_artifact", return_value=None):
            status = service.status()
    assert status["inference"] == "train_required"
    assert status["status"] == "unavailable"
    assert status["artifact_exists"] is False
    assert status["utility_kind"] == "gas"


def test_missing_artifact_raises_validation(tmp_path: Path) -> None:
    missing = tmp_path / "missing.pkl"
    service = GasForecastService(MagicMock())
    with patch.object(GasForecastService, "_artifact_path_for_zone", return_value=missing):
        with patch("app.services.gas_forecast_service.load_artifact", return_value=None):
            with pytest.raises(ValidationAppError, match="not trained"):
                service.predict()


def test_artifact_paths_isolated() -> None:
    assert PROPHET_GAS_DEMAND_PATH.name == "prophet_gas_demand.pkl"
    assert PROPHET_GAS_DEMAND_PATH != PROPHET_WATER_PATH
    assert PROPHET_GAS_DEMAND_PATH != PROPHET_POWER_LOAD_PATH
    assert PROPHET_GAS_DEMAND_PATH != GAS_XGBOOST_PATH


def test_demand_prefers_feeder_layer() -> None:
    seed_all()
    db = SessionLocal()
    try:
        asset_type, ids = resolve_gas_demand_asset_ids(db)
        assert asset_type == "gas_feeder"
        assert ids
        assert all(i.startswith("GFDR-") for i in ids)
        # Four-zone feeders present (A/B/C/D).
        zones = {i.split("-")[1] for i in ids}
        assert zones >= {"A", "B", "C", "D"}
    finally:
        db.close()


def test_dataset_filters_gas_only_and_orders() -> None:
    seed_all()
    db = SessionLocal()
    try:
        sim = GasAssetSimulator(db, seed=3, mode=GasScenarioMode.MIXED)
        for _ in range(15):
            sim.persist_tick()
        series, meta = GasDemandDatasetBuilder(db).build_demand_series()
        assert meta["utility_kind"] == "gas"
        assert meta["metric_key"] == GAS_DEMAND_METRIC_KEY
        assert meta["aggregation"] == "sum"
        assert meta["asset_type"] == "gas_feeder"
        assert not any(str(a).startswith("PMP") for a in (meta.get("asset_ids") or []))
        assert not any(str(a).startswith("TR-") for a in (meta.get("asset_ids") or []))
        if not series.empty:
            times = list(series["time"])
            assert times == sorted(times)
            assert (series["demand"] >= 0).all()
            assert "scenario" not in series.columns
            assert "zone" not in series.columns
            assert "health" not in series.columns
    finally:
        db.close()


def test_zone_c_and_d_not_dropped() -> None:
    seed_all()
    db = SessionLocal()
    try:
        for zone in ("zone-a", "zone-b", "zone-c", "zone-d"):
            asset_type, ids = resolve_gas_demand_asset_ids(db, zone_id=zone)
            assert asset_type == "gas_feeder"
            assert len(ids) >= 1
            letter = zone.split("-")[1].upper()
            assert all(f"-{letter}-" in i for i in ids)
    finally:
        db.close()


def test_simulator_can_feed_training(tmp_path: Path) -> None:
    seed_all()
    db = SessionLocal()
    path = tmp_path / "prophet_gas_from_sim.pkl"
    try:
        sim = GasAssetSimulator(db, seed=11, mode=GasScenarioMode.MIXED)
        for _ in range(25):
            sim.persist_tick()
        service = GasForecastService(db)
        series, meta = service._datasets.build_demand_series()
        if len(series) < 10:
            pytest.skip(f"Insufficient sim history for train: rows={len(series)}")
        artifact = train_gas_prophet(
            series,
            demand_source={
                "utility_kind": "gas",
                "metric_key": meta.get("metric_key"),
                "aggregation": meta.get("aggregation"),
                "asset_type": meta.get("asset_type"),
            },
            resample_freq=str(meta.get("resample_freq") or "1s"),
            artifact_path=path,
        )
        assert path.exists()
        forecast = predict_gas_prophet(artifact, periods=12)
        assert len(forecast) >= 1
        recent_mean = float(series["demand"].tail(5).mean())
        assert float(forecast["yhat"].iloc[0]) >= 0.0
        assert float(forecast["yhat"].iloc[0]) < recent_mean * 5.0 + 500.0
    finally:
        db.close()


def test_api_train_status_forecast(tmp_path: Path) -> None:
    seed_all()
    db = SessionLocal()
    try:
        sim = GasAssetSimulator(db, seed=21, mode=GasScenarioMode.MIXED)
        for _ in range(30):
            sim.persist_tick()
        result = GasForecastService(db).train()
        assert result["status"] == "trained"
        assert result["model"] == "gas_demand_forecast"
        assert PROPHET_GAS_DEMAND_PATH.exists()
        assert PROPHET_GAS_DEMAND_PATH.name != PROPHET_WATER_PATH.name
        assert PROPHET_GAS_DEMAND_PATH.name != GAS_XGBOOST_PATH.name
    finally:
        db.close()

    client = TestClient(app)
    status = client.get("/api/predictions/gas/forecast/status")
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["model"] == "gas_demand_forecast"
    assert body["artifact_exists"] is True
    assert body["inference"] == "available"
    assert body["target_metric"] == GAS_DEMAND_METRIC_KEY

    forecast = client.get("/api/predictions/gas/forecast")
    assert forecast.status_code == 200, forecast.text
    payload = forecast.json()
    assert payload["source"] == "model_forecast"
    assert payload["provider"] == "prophet"
    assert payload["unit"] == "Nm³/h"
    assert payload["forecast_points"]
    assert all(p.get("kind") == "forecast" for p in payload["forecast_points"])
    if payload.get("recent_actual"):
        assert all(p.get("kind") == "actual" for p in payload["recent_actual"])


def test_ml_status_includes_gas_demand_forecast(client: TestClient) -> None:
    resp = client.get("/api/ml/status")
    assert resp.status_code == 200
    names = {m["model"] for m in resp.json().get("models", [])}
    assert "gas_demand_forecast" in names


def test_no_forecast_recommendations_auto_created() -> None:
    """Gas Prophet must not mint OI drafts merely because a forecast exists."""
    import inspect

    from app.services import operational_intelligence_service as oi
    from app.services import recommendation_service as rec

    oi_src = inspect.getsource(oi.OperationalIntelligenceService)
    rec_src = inspect.getsource(rec.RecommendationService)
    assert "GasForecastService" not in oi_src
    assert "gas_demand_forecast" not in oi_src
    assert "GasForecastService" not in rec_src
