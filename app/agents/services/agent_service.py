"""AgentService — executes the LangGraph operational-summary workflow."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.agents.graph import build_operational_graph
from app.agents.state import initial_state
from app.observability.langfuse import flush_traces, start_agent_trace


class AgentService:
    """
    Thin execution façade over the compiled LangGraph workflow.

    Owns no business logic: it starts a Langfuse trace, runs the graph
    (which delegates to existing services), and shapes the API response.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def operational_summary(self) -> dict[str, Any]:
        started = time.perf_counter()
        generated_at = datetime.now(timezone.utc)

        trace = start_agent_trace(
            "operational-summary",
            metadata={"utility": "water", "workflow": "operational_summary_v1"},
        )
        try:
            graph = build_operational_graph(self._db, trace)
            state = graph.invoke(initial_state())
        finally:
            flush_traces()

        execution_time_ms = round((time.perf_counter() - started) * 1000.0, 1)
        return {
            "telemetry": state.get("telemetry", {}),
            "forecast": state.get("forecast", {}),
            "maintenance": state.get("maintenance", {}),
            "recommendations": state.get("recommendations", []),
            "priorities": state.get("priorities", []),
            "summary": state.get("summary", ""),
            "generated_at": generated_at,
            "execution_time_ms": execution_time_ms,
            "execution_status": state.get("execution_status", {}),
            "metadata": {
                **state.get("metadata", {}),
                "workflow": "operational_summary_v1",
                "nodes": [
                    "telemetry",
                    "forecast",
                    "maintenance",
                    "recommendation",
                    "prioritization",
                    "summary",
                ],
            },
        }
