"""Maintenance node — collects XGBoost predictions via PredictiveMaintenanceService."""

from __future__ import annotations

from typing import Any, Callable

from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.agents.state import AgentState
from app.core.errors import AppError
from app.observability.langfuse import node_span
from app.services.predictive_maintenance_service import PredictiveMaintenanceService


def build_maintenance_node(
    db: Session,
    trace: Any | None,
) -> Callable[[AgentState], dict[str, Any]]:
    def maintenance_node(state: AgentState) -> dict[str, Any]:
        with node_span(trace, "maintenance_node") as span:
            service = PredictiveMaintenanceService(db)
            try:
                predictions = jsonable_encoder(service.predict_all())
            except AppError as exc:
                maintenance = {
                    "available": False,
                    "reason": str(exc),
                    "predictions": [],
                    "highest_risk_assets": [],
                }
                span["output"] = {"available": False}
                return {
                    "maintenance": maintenance,
                    "execution_status": {"maintenance_node": "unavailable"},
                }

            highest_risk = [
                p for p in predictions if p.get("risk_level") in {"high", "critical"}
            ]
            maintenance = {
                "available": True,
                "model_version": predictions[0]["model_version"] if predictions else None,
                "predictions": predictions,
                "highest_risk_assets": [
                    {
                        "asset_id": p["asset_id"],
                        "asset_name": p["asset_name"],
                        "maintenance_risk": p["maintenance_risk"],
                        "risk_level": p["risk_level"],
                        "predicted_failure_days": p["predicted_failure_days"],
                    }
                    for p in highest_risk
                ],
            }
            span["output"] = {
                "available": True,
                "predictions": len(predictions),
                "highest_risk_assets": len(highest_risk),
            }
            return {
                "maintenance": maintenance,
                "execution_status": {"maintenance_node": "completed"},
            }

    return maintenance_node
