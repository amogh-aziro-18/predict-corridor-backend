"""
Cross-utility dependency impact assessment (Phase 16.1).

Deterministic graph traversal over explicit AssetDependency edges only.
Does not mutate telemetry, asset health, alerts, work orders, or scenarios.
Does not invent zone/utility-wide outages — only lists explicitly reachable dependents.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationAppError
from app.domain.enums import DependencyType
from app.domain.repositories import AssetDependencyRepository, AssetRepository

# Bounded BFS depth for transitive supplies traversal (root = depth 0).
DEFAULT_MAX_DEPTH = 5

# Explicit trigger vocabulary — not a new health model.
ALLOWED_CONDITIONS = frozenset(
    {
        "unavailable",
        "degraded",
        "critical",
        "at_risk",
    }
)

_CONDITION_ALIASES = {
    "offline": "unavailable",
    "failed": "unavailable",
    "high_risk": "at_risk",
    "at risk": "at_risk",
}


class CrossUtilityImpactService:
    """Analytical impact assessment from explicit dependency edges."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._assets = AssetRepository(db)
        self._deps = AssetDependencyRepository(db)

    def assess_asset_impact(
        self,
        *,
        asset_id: str,
        condition: str,
        max_depth: int | None = None,
        dependency_type: str = DependencyType.SUPPLIES.value,
    ) -> dict[str, Any]:
        """
        If ``asset_id`` is in the given condition, which assets reached via
        outgoing ``supplies`` edges could be potentially affected?

        Direction: source --supplies--> target
        Impact of source includes target (and transitive targets).
        Impact of target does NOT include source.
        """
        asset = self._assets.get(asset_id)
        if asset is None:
            raise NotFoundError(f"Asset not found: {asset_id}")

        trigger = self._normalize_condition(condition)
        depth_limit = DEFAULT_MAX_DEPTH if max_depth is None else int(max_depth)
        if depth_limit < 0:
            raise ValidationAppError("max_depth must be >= 0")
        if depth_limit > 20:
            raise ValidationAppError("max_depth must be <= 20")

        adjacency = self._outgoing_adjacency(dependency_type=dependency_type)
        direct, propagated, paths = self._traverse(
            root_id=asset.id,
            adjacency=adjacency,
            max_depth=depth_limit,
        )

        affected_assets = [*direct, *propagated]
        utilities = sorted({a["utility"] for a in affected_assets})
        zones = sorted({a["zone"] for a in affected_assets})

        explanation = self._explain(
            source_id=asset.id,
            trigger=trigger,
            direct_count=len(direct),
            propagated_count=len(propagated),
            utilities=utilities,
            zones=zones,
            max_depth=depth_limit,
        )

        return {
            "source_asset_id": asset.id,
            "source_asset_name": asset.name,
            "source_utility": asset.utility_kind,
            "source_zone": asset.zone_id,
            "trigger_condition": trigger,
            "max_depth": depth_limit,
            "dependency_type": dependency_type,
            "assessment_kind": "explicit_dependency_impact",
            "physics_based": False,
            "mutates_operational_state": False,
            "direct_impacts": direct,
            "propagated_impacts": propagated,
            "affected_assets": affected_assets,
            "affected_utilities": utilities,
            "affected_zones": zones,
            "affected_asset_count": len(affected_assets),
            "affected_utility_count": len(utilities),
            "affected_zone_count": len(zones),
            "dependency_paths": paths,
            "explanation": explanation,
            "notes": [
                "Impact uses explicit asset_dependencies edges only.",
                "Dependent assets are marked potentially affected — not declared operationally failed.",
                "No telemetry, alerts, work orders, or asset health were modified.",
            ],
        }

    def _normalize_condition(self, condition: str) -> str:
        raw = (condition or "").strip().lower().replace("-", "_")
        raw = _CONDITION_ALIASES.get(raw, raw)
        raw = raw.replace(" ", "_")
        if raw not in ALLOWED_CONDITIONS:
            allowed = ", ".join(sorted(ALLOWED_CONDITIONS))
            raise ValidationAppError(
                f"Unsupported impact condition '{condition}'. Allowed: {allowed}."
            )
        return raw

    def _outgoing_adjacency(self, *, dependency_type: str) -> dict[str, list[tuple[str, str]]]:
        """Map source_asset_id → list of (target_asset_id, dependency_id)."""
        rows = self._deps.list(dependency_type=dependency_type, enabled=True)
        adj: dict[str, list[tuple[str, str]]] = {}
        for edge in rows:
            adj.setdefault(edge.source_asset_id, []).append(
                (edge.target_asset_id, edge.id)
            )
        return adj

    def _traverse(
        self,
        *,
        root_id: str,
        adjacency: dict[str, list[tuple[str, str]]],
        max_depth: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """
        BFS from root along outgoing supplies edges.

        Returns (direct_impacts, propagated_impacts, dependency_paths).
        """
        if max_depth == 0:
            return [], [], []

        direct: list[dict[str, Any]] = []
        propagated: list[dict[str, Any]] = []
        paths: list[dict[str, Any]] = []
        seen: set[str] = {root_id}

        # queue items: (asset_id, depth, path_asset_ids, path_edge_ids)
        queue: deque[tuple[str, int, list[str], list[str]]] = deque()
        queue.append((root_id, 0, [root_id], []))

        while queue:
            current, depth, path_assets, path_edges = queue.popleft()
            if depth >= max_depth:
                continue
            for target_id, edge_id in adjacency.get(current, []):
                if target_id in seen:
                    # Cycle / already visited — skip to prevent infinite traversal.
                    continue
                seen.add(target_id)
                target = self._assets.get(target_id)
                if target is None:
                    continue
                next_depth = depth + 1
                next_path_assets = [*path_assets, target_id]
                next_path_edges = [*path_edges, edge_id]
                impact_kind = "direct" if next_depth == 1 else "propagated"
                impact_row = {
                    "asset_id": target.id,
                    "asset_name": target.name,
                    "utility": target.utility_kind,
                    "zone": target.zone_id,
                    "impact_kind": impact_kind,
                    "depth": next_depth,
                    "dependency_id": edge_id,
                    "via_asset_id": current,
                    "posture": "potentially_affected",
                    "reason": (
                        f"Explicit supplies edge from {current} → {target.id}; "
                        f"source trigger may leave this dependent asset potentially affected."
                    ),
                }
                if impact_kind == "direct":
                    direct.append(impact_row)
                else:
                    propagated.append(impact_row)
                paths.append(
                    {
                        "asset_id": target.id,
                        "depth": next_depth,
                        "impact_kind": impact_kind,
                        "path_asset_ids": next_path_assets,
                        "path_dependency_ids": next_path_edges,
                    }
                )
                queue.append((target_id, next_depth, next_path_assets, next_path_edges))

        direct.sort(key=lambda r: r["asset_id"])
        propagated.sort(key=lambda r: (r["depth"], r["asset_id"]))
        paths.sort(key=lambda r: (r["depth"], r["asset_id"]))
        return direct, propagated, paths

    @staticmethod
    def _explain(
        *,
        source_id: str,
        trigger: str,
        direct_count: int,
        propagated_count: int,
        utilities: list[str],
        zones: list[str],
        max_depth: int,
    ) -> str:
        total = direct_count + propagated_count
        if total == 0:
            return (
                f"No explicit outgoing dependency edges from {source_id} within "
                f"max_depth={max_depth}. No dependent assets identified as potentially affected "
                f"for trigger condition '{trigger}'."
            )
        util_txt = ", ".join(utilities) if utilities else "none"
        zone_txt = ", ".join(zones) if zones else "none"
        return (
            f"If {source_id} is '{trigger}', {total} explicitly dependent asset(s) are "
            f"identified as potentially affected "
            f"({direct_count} direct, {propagated_count} propagated, max_depth={max_depth}). "
            f"Affected utilities: {util_txt}. Affected zones: {zone_txt}. "
            "This is deterministic dependency impact analysis — not a declared multi-utility outage."
        )
