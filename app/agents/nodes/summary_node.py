"""
Summary node — concise operational summary of the collected state.

The summary only explains and prioritizes what the deterministic services
already produced. When an LLM is configured (OPENAI_API_KEY) it phrases the
summary; otherwise a deterministic template is used. In both cases the
underlying facts come exclusively from the agent state.
"""

from __future__ import annotations

from typing import Any, Callable

from app.agents.state import AgentState
from app.core.config import get_settings
from app.core.logging import get_logger
from app.observability.langfuse import node_span

logger = get_logger(__name__)


def _deterministic_summary(state: AgentState) -> str:
    telemetry = state.get("telemetry", {})
    forecast = state.get("forecast", {})
    maintenance = state.get("maintenance", {})
    priorities = state.get("priorities", [])
    metadata = state.get("metadata", {})

    parts: list[str] = []

    health = telemetry.get("overall_health")
    if health is not None:
        condition = "healthy" if health >= 80 else "degraded" if health >= 60 else "critical"
        parts.append(
            f"Water corridor is {condition} with overall health {health:.1f}%, "
            f"{telemetry.get('critical_assets', 0)} critical asset(s) and "
            f"{telemetry.get('active_alerts', 0)} active alert(s)."
        )

    trend = metadata.get("demand_trend")
    if forecast.get("available") and forecast.get("current_demand") is not None:
        trend_text = {
            "increasing": "demand is expected to increase over the next 24 hours",
            "decreasing": "demand is expected to decrease over the next 24 hours",
            "stable": "demand is expected to remain stable over the next 24 hours",
        }.get(trend or "", "a 48-hour demand forecast is available")
        parts.append(
            f"Current demand is {forecast['current_demand']:.1f} {forecast.get('unit', 'L/s')} "
            f"and {trend_text} (model {forecast.get('model_version')})."
        )

    high_risk = maintenance.get("highest_risk_assets", [])
    if high_risk:
        names = ", ".join(
            f"{a['asset_name']} (risk {a['maintenance_risk']:.2f})" for a in high_risk[:3]
        )
        parts.append(f"Predictive maintenance flags elevated risk for: {names}.")
    elif maintenance.get("available"):
        parts.append("No pump currently shows high predictive-maintenance risk.")

    if priorities:
        top = priorities[0]
        parts.append(
            f"Top operational priority: {top.get('title')} "
            f"(urgency: {top.get('urgency')}). Recommended action: {top.get('action')}"
        )
        if len(priorities) > 1:
            parts.append(
                f"{len(priorities)} prioritized recommendation(s) in total; "
                "see priorities for the ranked list."
            )
    else:
        parts.append("No active recommendations — operations are within normal parameters.")

    return " ".join(parts)


def _llm_summary(state: AgentState, deterministic: str) -> str | None:
    """Optional LLM phrasing. Facts are constrained to the deterministic digest."""
    settings = get_settings()
    if not settings.openai_api_key:
        return None
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        response = client.chat.completions.create(
            model=settings.agent_llm_model,
            temperature=0.2,
            max_tokens=300,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an operations analyst for a water utility corridor. "
                        "Rewrite the provided operational digest as a concise summary. "
                        "Only summarize, explain, and prioritize the given facts. "
                        "Never invent maintenance actions, assets, or numbers."
                    ),
                },
                {"role": "user", "content": deterministic},
            ],
        )
        content = response.choices[0].message.content
        return content.strip() if content else None
    except Exception:
        logger.exception("LLM summary failed; using deterministic summary")
        return None


def build_summary_node(
    trace: Any | None,
) -> Callable[[AgentState], dict[str, Any]]:
    def summary_node(state: AgentState) -> dict[str, Any]:
        with node_span(trace, "summary_node") as span:
            deterministic = _deterministic_summary(state)
            llm_text = _llm_summary(state, deterministic)
            summary = llm_text or deterministic
            engine = "llm" if llm_text else "deterministic_template"

            span["output"] = {"summary_engine": engine, "summary_length": len(summary)}
            return {
                "summary": summary,
                "metadata": {"summary_engine": engine},
                "execution_status": {"summary_node": "completed"},
            }

    return summary_node
