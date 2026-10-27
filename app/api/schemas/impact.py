"""Cross-utility impact assessment schemas (Phase 16.1)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AssetImpactRequest(BaseModel):
    asset_id: str = Field(min_length=1)
    condition: str = Field(
        min_length=1,
        description="Explicit trigger: unavailable | degraded | critical | at_risk",
    )
    max_depth: int | None = Field(
        default=None,
        description="Optional BFS depth bound (default 5, max 20). Root depth = 0.",
    )


class ImpactedAssetOut(BaseModel):
    asset_id: str
    asset_name: str
    utility: str
    zone: str
    impact_kind: str  # direct | propagated
    depth: int
    dependency_id: str
    via_asset_id: str
    posture: str
    reason: str


class DependencyPathOut(BaseModel):
    asset_id: str
    depth: int
    impact_kind: str
    path_asset_ids: list[str] = Field(default_factory=list)
    path_dependency_ids: list[str] = Field(default_factory=list)


class AssetImpactAssessmentOut(BaseModel):
    source_asset_id: str
    source_asset_name: str
    source_utility: str
    source_zone: str
    trigger_condition: str
    max_depth: int
    dependency_type: str
    assessment_kind: str = "explicit_dependency_impact"
    physics_based: bool = False
    mutates_operational_state: bool = False
    direct_impacts: list[ImpactedAssetOut] = Field(default_factory=list)
    propagated_impacts: list[ImpactedAssetOut] = Field(default_factory=list)
    affected_assets: list[ImpactedAssetOut] = Field(default_factory=list)
    affected_utilities: list[str] = Field(default_factory=list)
    affected_zones: list[str] = Field(default_factory=list)
    affected_asset_count: int = 0
    affected_utility_count: int = 0
    affected_zone_count: int = 0
    dependency_paths: list[DependencyPathOut] = Field(default_factory=list)
    explanation: str
    notes: list[str] = Field(default_factory=list)
