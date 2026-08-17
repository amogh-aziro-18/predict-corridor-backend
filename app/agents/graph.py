"""
LangGraph workflow definition for the operational-intelligence agent.

Sequential pipeline over existing services:

    START -> telemetry -> forecast -> maintenance
          -> recommendation -> prioritization -> summary -> END

The graph is utility-agnostic: nodes delegate to services, so supporting
Power/Gas/Roads/Environment later means swapping service implementations,
not changing this orchestration.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.agents.nodes import (
    build_forecast_node,
    build_maintenance_node,
    build_prioritization_node,
    build_recommendation_node,
    build_summary_node,
    build_telemetry_node,
)
from app.agents.state import AgentState

NODE_ORDER = [
    "telemetry",
    "forecast",
    "maintenance",
    "recommendation",
    "prioritization",
    "summary",
]


def build_operational_graph(db: Session, trace: Any | None = None):
    """Compile the operational-summary workflow bound to one DB session."""
    graph = StateGraph(AgentState)

    graph.add_node("telemetry", build_telemetry_node(db, trace))
    graph.add_node("forecast", build_forecast_node(db, trace))
    graph.add_node("maintenance", build_maintenance_node(db, trace))
    graph.add_node("recommendation", build_recommendation_node(db, trace))
    graph.add_node("prioritization", build_prioritization_node(trace))
    graph.add_node("summary", build_summary_node(trace))

    graph.add_edge(START, NODE_ORDER[0])
    for previous, current in zip(NODE_ORDER, NODE_ORDER[1:]):
        graph.add_edge(previous, current)
    graph.add_edge(NODE_ORDER[-1], END)

    return graph.compile()
