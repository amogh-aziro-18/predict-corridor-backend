"""POC work-order seed — small open queue for Operations demos.

All rows start as status=open with no assignee so operators can demonstrate
assign → in_progress → completed → closed via WorkOrderService.
"""

from __future__ import annotations

from typing import Any

from app.domain.enums import WorkOrderSource, WorkOrderStatus

# Stable IDs for idempotent merge. Asset IDs must exist in WATER_ASSETS seed.
WORK_ORDERS: list[dict[str, Any]] = [
    {
        "id": "WO-SEED-001",
        "asset_id": "PMP-C-01",
        "title": "Inspect pump after pressure excursion",
        "description": "POC seed work order — open queue item for Zone C pump.",
        "priority": "high",
    },
    {
        "id": "WO-SEED-002",
        "asset_id": "TNK-A-01",
        "title": "Tank level sensor verification",
        "description": "POC seed work order — open queue item for Zone A tank.",
        "priority": "medium",
    },
    {
        "id": "WO-SEED-003",
        "asset_id": "PMP-B-01",
        "title": "Pump vibration follow-up inspection",
        "description": "POC seed work order — open queue item for Zone B pump.",
        "priority": "medium",
    },
]

SEED_WORK_ORDER_STATUS = WorkOrderStatus.OPEN.value
SEED_WORK_ORDER_SOURCE = WorkOrderSource.MANUAL.value
SEED_CREATED_BY = "db-seed"
