"""Explicit POC dependency assumptions for cross-utility simulation (Phase 16.0).

These edges are configuration — NOT discovered infrastructure facts.
They are not inferred from zone co-location, asset type, naming, or profile kW ratings.

Semantics: source --supplies--> target  means  target depends on source.
"""

from __future__ import annotations

from typing import Any

from app.domain.enums import DependencyType

# Explicit POC dependency assumptions for cross-utility simulation.
DEPENDENCY_EDGES: list[dict[str, Any]] = [
    {
        "id": "DEP-POC-001",
        "source_asset_id": "FDR-A-01",
        "target_asset_id": "PMP-A-01",
        "dependency_type": DependencyType.SUPPLIES.value,
        "enabled": True,
        # Explicit POC assumption: Zone A feeder supplies Zone A high-lift pump.
    },
    {
        "id": "DEP-POC-002",
        "source_asset_id": "FDR-A-01",
        "target_asset_id": "CMP-A-01",
        "dependency_type": DependencyType.SUPPLIES.value,
        "enabled": True,
        # Explicit POC assumption: Zone A feeder supplies Zone A gas compressor.
    },
    {
        "id": "DEP-POC-003",
        "source_asset_id": "FDR-C-01",
        "target_asset_id": "PMP-C-01",
        "dependency_type": DependencyType.SUPPLIES.value,
        "enabled": True,
        # Explicit POC assumption: Zone C feeder supplies Zone C pump (second Power→Water).
    },
    {
        "id": "DEP-POC-004",
        "source_asset_id": "FDR-A-01",
        "target_asset_id": "POP-A-01",
        "dependency_type": DependencyType.SUPPLIES.value,
        "enabled": True,
        # POC ASSUMPTION — NOT a discovered real-world electrical topology.
        # Zone A feeder electrically supplies Zone A Edge PoP (aggregation hut).
        # Electrical SUPPLIES only — do NOT use SUPPLIES for Communications→Water/Power/Gas.
    },
]

EXPECTED_DEPENDENCY_COUNT = len(DEPENDENCY_EDGES)
