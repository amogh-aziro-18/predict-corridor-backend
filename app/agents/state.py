"""
Typed LangGraph state for the operational-intelligence workflow.

Each node updates only its own portion of the state. Values are plain
JSON-safe dicts/lists produced by the existing services — the graph never
holds ORM objects or DataFrames.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class AgentState(TypedDict, total=False):
    # Populated by telemetry_node (TelemetryService + DashboardService output)
    telemetry: dict[str, Any]

    # Populated by forecast_node (ForecastService / Prophet output)
    forecast: dict[str, Any]

    # Populated by maintenance_node (PredictiveMaintenanceService / XGBoost output)
    maintenance: dict[str, Any]

    # Populated by recommendation_node (deterministic RecommendationService output)
    recommendations: list[dict[str, Any]]

    # Populated by prioritization_node (ranked view over existing outputs only)
    priorities: list[dict[str, Any]]

    # Populated by summary_node
    summary: str

    # Shared execution metadata — merged across nodes via dict-union reducers
    metadata: Annotated[dict[str, Any], operator.or_]
    execution_status: Annotated[dict[str, str], operator.or_]
    generated_at: str


def initial_state() -> AgentState:
    return AgentState(
        telemetry={},
        forecast={},
        maintenance={},
        recommendations=[],
        priorities=[],
        summary="",
        metadata={},
        execution_status={},
        generated_at="",
    )
