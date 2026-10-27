"""Unit tests for Prophet demand forecasting (controlled fixtures, no fragile live DB)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.api.schemas.forecast import DemandForecastOut, ForecastModelStatusOut
from app.core.errors import ValidationAppError
from app.main import app
from app.ml.forecasting.prophet_model import (
    load_artifact,
    predict_prophet,
    to_prophet_frame,
    train_prophet,
)
from app.services.forecast_service import ForecastService


def _synthetic_demand(hours: int = 36, freq: str = "5min") -> pd.DataFrame:
    """Deterministic non-zero demand series for Prophet unit tests."""
    end = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    index = pd.date_range(end=end, periods=hours * 12, freq=freq, tz="UTC")
    values = [80.0 + (i % 24) * 3.5 + (i % 7) for i in range(len(index))]
    return pd.DataFrame({"time": index, "demand": values})


def test_openapi_includes_forecast_and_status_routes() -> None:
    paths = app.openapi()["paths"]
    assert "/api/forecasts/demand" in paths
    assert "/api/forecasts/demand/status" in paths
    assert "/api/forecasts/demand/train" in paths
    assert "/api/ml/status" in paths  # XGBoost status unchanged


def test_to_prophet_frame_requires_columns() -> None:
    with pytest.raises(ValueError, match="time, demand"):
        to_prophet_frame(pd.DataFrame({"x": [1], "y": [2]}))


def test_train_prophet_rejects_insufficient_history(tmp_path: Path) -> None:
    series = _synthetic_demand(hours=1).iloc[:5]
    with pytest.raises(ValueError, match="Insufficient telemetry"):
        train_prophet(series, artifact_path=tmp_path / "prophet_test.pkl")


def test_train_predict_artifact_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "prophet_test.pkl"
    series = _synthetic_demand(hours=48)
    artifact = train_prophet(
        series,
        demand_source={"metric_key": "flow_rate", "aggregation": "sum"},
        artifact_path=path,
    )
    assert path.exists()
    assert artifact.model_version == "prophet_water_v1"
    assert artifact.training_window.get("rows", 0) >= 10

    loaded = load_artifact(path)
    assert loaded is not None
    assert loaded.model_version == artifact.model_version

    forecast = predict_prophet(loaded, hours=24)
    assert not forecast.empty
    assert {"ds", "yhat", "yhat_lower", "yhat_upper"}.issubset(forecast.columns)
    assert (forecast["yhat"] >= 0).all()
    assert (forecast["yhat_lower"] <= forecast["yhat"]).all()
    assert (forecast["yhat"] <= forecast["yhat_upper"]).all()


def test_missing_artifact_raises_validation(tmp_path: Path) -> None:
    db = MagicMock()
    service = ForecastService(db)
    missing = tmp_path / "missing.pkl"
    with patch.object(ForecastService, "_artifact_path_for_zone", return_value=missing):
        with patch("app.services.forecast_service.load_artifact", return_value=None):
            with patch(
                "app.services.forecast_service.PROPHET_WATER_PATH",
                missing,
            ):
                with pytest.raises(ValidationAppError, match="not trained"):
                    service.predict()


def test_status_reports_train_required_when_missing(tmp_path: Path) -> None:
    db = MagicMock()
    service = ForecastService(db)
    missing = tmp_path / "missing.pkl"
    with patch.object(ForecastService, "_artifact_path_for_zone", return_value=missing):
        with patch("app.services.forecast_service.load_artifact", return_value=None):
            status = service.status()
    assert status["artifact_exists"] is False
    assert status["inference"] == "train_required"
    assert status["model"] == "prophet_demand"
    ForecastModelStatusOut.model_validate(status)


def test_predict_payload_schema_and_horizon_order(tmp_path: Path) -> None:
    path = tmp_path / "prophet_payload.pkl"
    series = _synthetic_demand(hours=48)
    artifact = train_prophet(series, artifact_path=path)

    db = MagicMock()
    service = ForecastService(db)
    meta = {
        "asset_type": "flow_meter",
        "asset_ids": ["FLW-B-01"],
        "metric_key": "flow_rate",
        "aggregation": "sum",
    }

    with patch.object(service, "_ensure_artifact", return_value=artifact):
        with patch.object(
            service._datasets,
            "build_demand_series",
            return_value=(series, meta),
        ):
            payload = service.predict(hours=48)

    out = DemandForecastOut.model_validate(payload)
    assert out.current_demand is not None
    assert out.unit == "L/s"
    assert out.model_version == "prophet_water_v1"
    assert len(out.recent_actual) > 0
    points = out.forecast.next_24_hours
    assert len(points) >= 1
    hours = [p.hours_ahead for p in points]
    assert hours == sorted(hours)
    for p in points:
        assert p.demand >= 0
        assert p.lower <= p.demand <= p.upper


def test_zone_fallback_uses_corridor_artifact(tmp_path: Path) -> None:
    corridor = tmp_path / "prophet_water.pkl"
    series = _synthetic_demand(hours=36)
    artifact = train_prophet(series, artifact_path=corridor)

    db = MagicMock()
    # ZoneRepository.get must return a zone when training; for predict we only load.
    service = ForecastService(db)
    zone_path = tmp_path / "prophet_water_zone-z.pkl"

    with patch.object(
        ForecastService,
        "_artifact_path_for_zone",
        side_effect=lambda z: zone_path if z else corridor,
    ):
        with patch("app.services.forecast_service.load_artifact") as load:
            load.side_effect = lambda p=None: None if p == zone_path else artifact
            with patch(
                "app.services.forecast_service.PROPHET_WATER_PATH",
                corridor,
            ):
                loaded = service._ensure_artifact(zone_id="zone-z")
    assert loaded is artifact


def test_empty_demand_series_fails_train() -> None:
    db = MagicMock()
    service = ForecastService(db)
    with patch(
        "app.domain.repositories.ZoneRepository.get",
        return_value=None,
    ):
        # no zone_id → skip zone check
        pass
    with patch.object(
        service._datasets,
        "build_demand_series",
        return_value=(pd.DataFrame(columns=["time", "demand"]), {}),
    ):
        with pytest.raises(ValidationAppError, match="No demand telemetry"):
            service.train()
