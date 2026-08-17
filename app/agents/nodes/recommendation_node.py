"""Recommendation node — collects deterministic RecommendationService output."""

from __future__ import annotations

from typing import Any, Callable

from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.agents.state import AgentState
from app.observability.langfuse import node_span
from app.services.recommendation_service import RecommendationService


def build_recommendation_node(
    db: Session,
    trace: Any | None,
) -> Callable[[AgentState], dict[str, Any]]:
    def recommendation_node(state: AgentState) -> dict[str, Any]:
        with node_span(trace, "recommendation_node") as span:
            # RecommendationService remains the single source of truth;
            # the node only snapshots its output into the agent state.
            recommendations = jsonable_encoder(
                RecommendationService(db).list_recommendations()
            )
            span["output"] = {
                "recommendations": len(recommendations),
                "priorities": sorted({r.get("priority", "") for r in recommendations}),
            }
            return {
                "recommendations": recommendations,
                "execution_status": {"recommendation_node": "completed"},
            }

    return recommendation_node
