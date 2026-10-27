"""Cross-utility asset dependency API schemas (Phase 16.0)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DependencyEdgeOut(BaseModel):
    id: str
    source_asset_id: str
    source_utility: str
    source_zone: str
    target_asset_id: str
    target_utility: str
    target_zone: str
    dependency_type: str
    enabled: bool


class DependencyEndpointRefOut(BaseModel):
    dependency_id: str
    asset_id: str
    asset_name: str
    utility: str
    zone: str
    dependency_type: str


class AssetDependenciesOut(BaseModel):
    asset_id: str
    utility: str
    zone: str
    available: bool = True
    depends_on: list[DependencyEndpointRefOut] = Field(default_factory=list)
    depended_by: list[DependencyEndpointRefOut] = Field(default_factory=list)
    edges: list[DependencyEdgeOut] = Field(default_factory=list)
