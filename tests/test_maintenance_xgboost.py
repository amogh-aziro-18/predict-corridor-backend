"""Unit tests for XGBoost predictive maintenance (controlled fixtures)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from app.api.schemas.maintenance_prediction import (
    MaintenanceModelStatusOut,
    MaintenancePredictionOut,
)
from app.core.errors import ValidationAppError
from app.main import app
from app.ml.maintenance.labeling import (
    LABEL_COLUMN,
    add_maintenance_labels,
    label_distribution,
)
from app.ml.maintenance.xgboost_model import (
    load_artifact,
    predict_xgboost,
    train_xgboost,
)
from app.services.predictive_maintenance_service import PredictiveMaintenanceService


def _feature_frame(*, n: int = 40, risk_half: bool = True) -> pd.DataFrame:
    """Synthetic pump feature panel with both risk and normal regimes."""
    rows: list[dict] = []
    now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    for i in range(n):
        risky = risk_half and i % 2 == 0
        rows.append(
            {
                "time": now,
                "asset_id": f"PMP-T-{i % 4:02d}",
                "asset_name": f"Test Pump {i % 4}",
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
    return pd.DataFrame(rows)


def test_openapi_includes_maintenance_routes() -> None:
    paths = app.openapi()["paths"]
    assert "/api/predictions/maintenance" in paths
    assert "/api/predictions/maintenance/train" in paths
    assert "/api/ml/status" in paths


def test_labeling_requires_corroborating_signals() -> None:
    mild = _feature_frame(n=4, risk_half=False)
    mild.loc[:, "pressure"] = 1.5  # warning only — score < 5
    labelled = add_maintenance_labels(mild)
    assert int(labelled[LABEL_COLUMN].sum()) == 0

    harsh = _feature_frame(n=8, risk_half=True)
    labelled = add_maintenance_labels(harsh)
    dist = label_distribution(labelled)
    assert dist["maintenance_risk"] > 0
    assert dist["normal"] > 0


def test_train_rejects_single_class(tmp_path: Path) -> None:
    frame = _feature_frame(n=20, risk_half=False)
    labelled = add_maintenance_labels(frame)
    with pytest.raises(ValueError, match="both normal and maintenance-risk"):
        train_xgboost(labelled, artifact_path=tmp_path / "xgb.pkl")


def test_train_predict_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "pump_xgboost_test.pkl"
    labelled = add_maintenance_labels(_feature_frame(n=48, risk_half=True))
    artifact = train_xgboost(labelled, artifact_path=path)
    assert path.exists()
    assert artifact.model_version.startswith("pump_xgboost")
    assert artifact.training_window.get("rows", 0) >= 10

    loaded = load_artifact(path)
    assert loaded is not None
    probs, contribs = predict_xgboost(loaded, labelled.head(4))
    assert len(probs) == 4
    assert np.all(probs >= 0.0) and np.all(probs <= 1.0)
    assert contribs.shape[0] == 4
    assert contribs.shape[1] == len(loaded.feature_names)


def test_missing_artifact_raises(tmp_path: Path) -> None:
    service = PredictiveMaintenanceService(MagicMock())
    missing = tmp_path / "missing.pkl"
    with patch("app.services.predictive_maintenance_service.PUMP_XGBOOST_PATH", missing):
        with patch(
            "app.services.predictive_maintenance_service.load_artifact",
            return_value=None,
        ):
            with pytest.raises(ValidationAppError, match="not trained"):
                service.predict_all()


def test_empty_features_train_fails() -> None:
    service = PredictiveMaintenanceService(MagicMock())
    with patch.object(
        service,
        "prepare_features",
        return_value=pd.DataFrame(),
    ):
        with pytest.raises(ValidationAppError, match="No pump telemetry"):
            service.train()


def test_status_schema_includes_related_models(tmp_path: Path) -> None:
    service = PredictiveMaintenanceService(MagicMock())
    missing = tmp_path / "none.pkl"
    with patch("app.services.predictive_maintenance_service.PUMP_XGBOOST_PATH", missing):
        with patch(
            "app.services.predictive_maintenance_service.load_artifact",
            return_value=None,
        ):
            with patch(
                "app.services.predictive_maintenance_service.load_prophet_artifact",
                return_value=None,
            ):
                with patch(
                    "app.services.predictive_maintenance_service.PROPHET_WATER_PATH",
                    tmp_path / "prophet_water.pkl",
                ):
                    status = service.status()
    out = MaintenanceModelStatusOut.model_validate(status)
    assert out.artifact_exists is False
    assert len(out.models) == 2
    names = {m.model for m in out.models}
    assert "prophet_demand" in names
    assert "pump_xgboost" in names


def test_predict_payload_schema_and_risk_bounds(tmp_path: Path) -> None:
    path = tmp_path / "xgb_payload.pkl"
    labelled = add_maintenance_labels(_feature_frame(n=40, risk_half=True))
    artifact = train_xgboost(labelled, artifact_path=path)

    service = PredictiveMaintenanceService(MagicMock())
    latest = labelled.sort_values(["asset_id", "time"]).groupby("asset_id", as_index=False).tail(1)
    with patch.object(service, "_ensure_artifact", return_value=artifact):
        with patch.object(service, "prepare_features", return_value=latest):
            rows = service.predict_all()

    assert rows
    for row in rows:
        out = MaintenancePredictionOut.model_validate(row)
        assert 0.0 <= out.maintenance_risk <= 1.0
        assert out.risk_level in {"critical", "high", "medium", "low"}
        assert out.horizon_basis == "probability_mapped"
        assert out.predicted_failure_days >= 1


def test_risk_level_thresholds() -> None:
    assert PredictiveMaintenanceService._risk_level(0.9) == "critical"
    assert PredictiveMaintenanceService._risk_level(0.7) == "high"
    assert PredictiveMaintenanceService._risk_level(0.5) == "medium"
    assert PredictiveMaintenanceService._risk_level(0.2) == "low"


def test_maintenance_node_soft_fail_when_untrained() -> None:
    from app.agents.nodes.maintenance_node import build_maintenance_node

    db = MagicMock()
    node = build_maintenance_node(db, trace=None)
    with patch(
        "app.agents.nodes.maintenance_node.PredictiveMaintenanceService"
    ) as svc_cls:
        instance = svc_cls.return_value
        instance.predict_all.side_effect = ValidationAppError("XGBoost model not trained.")
        result = node({})
    assert result["maintenance"]["available"] is False
    assert result["execution_status"]["maintenance_node"] == "unavailable"


def test_maintenance_node_available_when_predictions_exist() -> None:
    from app.agents.nodes.maintenance_node import build_maintenance_node

    db = MagicMock()
    node = build_maintenance_node(db, trace=None)
    sample = [
        {
            "asset_id": "PMP-C-01",
            "asset_name": "Pump C",
            "affected_zone": "zone-c",
            "maintenance_risk": 0.9,
            "risk_level": "critical",
            "confidence": 0.9,
            "predicted_failure_days": 3,
            "horizon_basis": "probability_mapped",
            "top_contributing_features": [],
            "generated_at": datetime.now(timezone.utc),
            "model_version": "pump_xgboost_v2",
        }
    ]
    with patch(
        "app.agents.nodes.maintenance_node.PredictiveMaintenanceService"
    ) as svc_cls:
        svc_cls.return_value.predict_all.return_value = sample
        result = node({})
    assert result["maintenance"]["available"] is True
    assert len(result["maintenance"]["highest_risk_assets"]) == 1
