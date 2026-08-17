"""
Prioritization node — ranks existing recommendations by urgency.

Deterministic merge of telemetry, forecast, and maintenance context onto the
recommendations already produced by RecommendationService. This node never
invents new recommendations; it only orders and explains existing ones.
"""

from __future__ import annotations

from typing import Any, Callable

from app.agents.state import AgentState
from app.observability.langfuse import node_span

_PRIORITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}
_MAX_PRIORITIES = 8


def _demand_trend(forecast: dict[str, Any]) -> str | None:
    """Compare mean forecast demand over the next 24h with current demand."""
    if not forecast.get("available"):
        return None
    current = forecast.get("current_demand")
    points = (forecast.get("forecast") or {}).get("next_24_hours") or []
    values = [p.get("demand") for p in points if isinstance(p.get("demand"), (int, float))]
    if current is None or not values:
        return None
    mean_next = sum(values) / len(values)
    if mean_next >= current * 1.05:
        return "increasing"
    if mean_next <= current * 0.95:
        return "decreasing"
    return "stable"


def build_prioritization_node(
    trace: Any | None,
) -> Callable[[AgentState], dict[str, Any]]:
    def prioritization_node(state: AgentState) -> dict[str, Any]:
        with node_span(trace, "prioritization_node") as span:
            recommendations = state.get("recommendations", [])
            telemetry = state.get("telemetry", {})
            forecast = state.get("forecast", {})
            maintenance = state.get("maintenance", {})

            risk_by_asset = {
                p["asset_id"]: p
                for p in maintenance.get("predictions", [])
                if p.get("asset_id")
            }
            trend = _demand_trend(forecast)

            ranked = sorted(
                recommendations,
                key=lambda r: (
                    -_PRIORITY_RANK.get(r.get("priority", ""), 0),
                    -float(r.get("confidence", 0.0)),
                    str(r.get("title", "")),
                ),
            )

            priorities: list[dict[str, Any]] = []
            for index, rec in enumerate(ranked[:_MAX_PRIORITIES], start=1):
                reasons: list[str] = []
                if rec.get("description"):
                    reasons.append(str(rec["description"]))

                prediction = risk_by_asset.get(rec.get("affected_asset") or "")
                if prediction and prediction.get("risk_level") in {"high", "critical"}:
                    reasons.append(
                        f"Predictive maintenance risk {prediction['maintenance_risk']:.2f} "
                        f"({prediction['risk_level']}); estimated "
                        f"{prediction['predicted_failure_days']} day(s) to failure."
                    )
                if trend == "increasing":
                    reasons.append(
                        "Forecast indicates increasing corridor demand over the next 24 hours."
                    )
                if telemetry.get("critical_assets"):
                    reasons.append(
                        f"{telemetry['critical_assets']} critical asset(s) currently flagged "
                        "by live telemetry."
                    )

                priorities.append(
                    {
                        "rank": index,
                        "recommendation_id": rec.get("id"),
                        "title": rec.get("title"),
                        "urgency": rec.get("priority"),
                        "severity": rec.get("severity"),
                        "affected_asset": rec.get("affected_asset"),
                        "affected_zone": rec.get("affected_zone"),
                        "action": rec.get("recommendation"),
                        "confidence": rec.get("confidence"),
                        "reasons": reasons,
                    }
                )

            span["output"] = {
                "priorities": len(priorities),
                "demand_trend": trend,
                "top_urgency": priorities[0]["urgency"] if priorities else None,
            }
            return {
                "priorities": priorities,
                "metadata": {"demand_trend": trend},
                "execution_status": {"prioritization_node": "completed"},
            }

    return prioritization_node
