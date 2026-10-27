"""Phase 14.3C2 — Power Prophet load forecasting tests."""

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
from app.ml.artifacts import PROPHET_POWER_LOAD_PATH, PROPHET_WATER_PATH
from app.ml.power.forecast_constants import MODEL_VERSION_POWER_PROPHET
from app.ml.power.forecast_dataset import PowerLoadDatasetBuilder
from app.ml.power.prophet_model import (
    load_artifact,
    predict_power_prophet,
    to_prophet_frame,
    train_power_prophet,
)
from app.services.power_forecast_service import PowerForecastService
from app.simulator.power.asset_simulator import PowerAssetSimulator
from app.simulator.power.scenarios import PowerScenarioMode


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _synthetic_load(hours: int = 36, freq: str = "1min") -> pd.DataFrame:
    end = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    index = pd.date_range(end=end, periods=hours * 60, freq=freq, tz="UTC")
    values = [90.0 + (i % 60) * 0.4 + (i % 11) * 0.2 for i in range(len(index))]
    return pd.DataFrame({"time": index, "demand": values})


def test_openapi_includes_power_forecast_routes(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/predictions/power/forecast" in paths
    assert "/api/predictions/power/forecast/train" in paths
    assert "/api/predictions/power/forecast/status" in paths


def test_to_prophet_frame_requires_columns() -> None:
    with pytest.raises(ValueError, match="time, demand"):
        to_prophet_frame(pd.DataFrame({"x": [1], "y": [2]}))


def test_to_prophet_frame_orders_and_dedupes() -> None:
    ts = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    frame = pd.DataFrame(
        {
            "time": [ts + timedelta(minutes=1), ts, ts],
            "demand": [12.0, 10.0, 14.0],
        }
    )
    out = to_prophet_frame(frame)
    assert list(out["ds"]) == sorted(out["ds"].tolist())
    assert len(out) == 2
    # Duplicate timestamp averaged.
    first = out.iloc[0]
    assert abs(float(first["y"]) - 12.0) < 1e-6


def test_train_rejects_insufficient_history(tmp_path: Path) -> None:
    series = _synthetic_load(hours=1).iloc[:5]
    with pytest.raises(ValueError, match="Insufficient Power telemetry"):
        train_power_prophet(series, artifact_path=tmp_path / "p.pkl")


def test_train_predict_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "prophet_power_load_test.pkl"
    series = _synthetic_load(hours=12)
    artifact = train_power_prophet(
        series,
        demand_source={
            "utility_kind": "power",
            "metric_key": "active_power_mw",
            "aggregation": "sum",
        },
        resample_freq="1min",
        artifact_path=path,
    )
    assert path.exists()
    assert artifact.model_version == MODEL_VERSION_POWER_PROPHET
    assert artifact.training_metadata["utility_kind"] == "power"
    assert "validation_metrics" in artifact.training_metadata

    loaded = load_artifact(path)
    assert loaded is not None
    forecast = predict_power_prophet(loaded, hours=6)
    assert not forecast.empty
    assert {"ds", "yhat", "yhat_lower", "yhat_upper"}.issubset(forecast.columns)
    assert (forecast["yhat"] >= 0).all()
    # Future timestamps only.
    hist_end = pd.Timestamp(loaded.training_window["end"]).tz_localize(None)
    assert (forecast["ds"] > hist_end).all()


def test_chronological_metrics_present_for_long_series(tmp_path: Path) -> None:
    path = tmp_path / "prophet_metrics.pkl"
    artifact = train_power_prophet(
        _synthetic_load(hours=24),
        resample_freq="1min",
        artifact_path=path,
    )
    metrics = artifact.training_metadata.get("validation_metrics") or {}
    note = artifact.training_metadata.get("validation_note")
    assert note == "chronological holdout"
    assert "mae" in metrics and "rmse" in metrics
    assert metrics["holdout_rows"] >= 5


def test_status_train_required_when_missing(tmp_path: Path) -> None:
    missing = tmp_path / "missing_power_prophet.pkl"
    db = MagicMock()
    service = PowerForecastService(db)
    with patch.object(PowerForecastService, "_artifact_path_for_zone", return_value=missing):
        with patch("app.services.power_forecast_service.load_artifact", return_value=None):
            status = service.status()
    assert status["inference"] == "train_required"
    assert status["artifact_exists"] is False
    assert status["utility_kind"] == "power"


def test_missing_artifact_raises_validation(tmp_path: Path) -> None:
    missing = tmp_path / "missing.pkl"
    service = PowerForecastService(MagicMock())
    with patch.object(PowerForecastService, "_artifact_path_for_zone", return_value=missing):
        with patch("app.services.power_forecast_service.load_artifact", return_value=None):
            with pytest.raises(ValidationAppError, match="not trained"):
                service.predict()


def test_water_artifact_path_untouched() -> None:
    assert PROPHET_POWER_LOAD_PATH.name == "prophet_power_load.pkl"
    assert PROPHET_WATER_PATH.name == "prophet_water.pkl"
    assert PROPHET_POWER_LOAD_PATH != PROPHET_WATER_PATH


def test_dataset_filters_power_only_and_orders() -> None:
    seed_all()
    db = SessionLocal()
    try:
        sim = PowerAssetSimulator(db, seed=3, mode=PowerScenarioMode.MIXED)
        for _ in range(15):
            sim.persist_tick()
        series, meta = PowerLoadDatasetBuilder(db).build_load_series()
        assert meta["utility_kind"] == "power"
        assert meta["metric_key"] == "active_power_mw"
        assert meta["aggregation"] == "sum"
        assert not any(str(a).startswith("PMP") for a in (meta.get("asset_ids") or []))
        if not series.empty:
            times = list(series["time"])
            assert times == sorted(times)
            assert (series["demand"] >= 0).all()
            # No scenario labels in series columns.
            assert "scenario" not in series.columns
    finally:
        db.close()


def test_simulator_can_feed_training(tmp_path: Path) -> None:
    seed_all()
    db = SessionLocal()
    path = tmp_path / "prophet_from_sim.pkl"
    try:
        sim = PowerAssetSimulator(db, seed=11, mode=PowerScenarioMode.MIXED)
        for _ in range(25):
            sim.persist_tick()
        service = PowerForecastService(db)
        series, meta = service._datasets.build_load_series()
        if len(series) < 10:
            pytest.skip(f"Insufficient sim history for train: rows={len(series)}")
        artifact = train_power_prophet(
            series,
            demand_source={
                "utility_kind": "power",
                "metric_key": meta.get("metric_key"),
                "aggregation": meta.get("aggregation"),
            },
            resample_freq=str(meta.get("resample_freq") or "1s"),
            artifact_path=path,
        )
        assert path.exists()
        forecast = predict_power_prophet(artifact, periods=12)
        assert len(forecast) >= 1
        # Plausible vs recent mean (within loose band — not exact).
        recent_mean = float(series["demand"].tail(5).mean())
        assert float(forecast["yhat"].iloc[0]) >= 0.0
        assert float(forecast["yhat"].iloc[0]) < recent_mean * 5.0 + 50.0
    finally:
        db.close()


def test_ml_status_includes_power_load_forecast(client: TestClient) -> None:
    resp = client.get("/api/ml/status")
    assert resp.status_code == 200
    names = {m["model"] for m in resp.json().get("models", [])}
    assert "power_load_forecast" in names
