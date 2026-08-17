"""Forecast node — collects Prophet demand forecast via ForecastService."""

from __future__ import annotations

from typing import Any, Callable

from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.agents.state import AgentState
from app.core.errors import AppError
from app.observability.langfuse import node_span
from app.services.forecast_service import ForecastService


def build_forecast_node(
    db: Session,
    trace: Any | None,
) -> Callable[[AgentState], dict[str, Any]]:
    def forecast_node(state: AgentState) -> dict[str, Any]:
        with node_span(trace, "forecast_node") as span:
            try:
                payload = ForecastService(db).predict(hours=48)
            except AppError as exc:
                # Model not trained yet — the workflow degrades gracefully
                # instead of failing the whole operational summary.
                forecast = {"available": False, "reason": str(exc)}
                span["output"] = {"available": False}
                return {
                    "forecast": forecast,
                    "execution_status": {"forecast_node": "unavailable"},
                }

            forecast = jsonable_encoder(payload)
            forecast["available"] = True
            span["output"] = {
                "available": True,
                "current_demand": forecast.get("current_demand"),
                "model_version": forecast.get("model_version"),
                "confidence": forecast.get("confidence"),
            }
            return {
                "forecast": forecast,
                "execution_status": {"forecast_node": "completed"},
            }

    return forecast_node
