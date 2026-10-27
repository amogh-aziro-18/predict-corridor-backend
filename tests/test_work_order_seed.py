"""Seed work-order catalog — idempotent open-queue POC rows."""

from __future__ import annotations

from app.db.seed import seed_all
from app.db.seed.work_orders_catalog import WORK_ORDERS
from app.db.session import SessionLocal
from app.domain.models import Asset, WorkOrder


def test_work_order_seed_idempotent_and_valid_assets() -> None:
    first = seed_all()
    assert first["work_orders"] == len(WORK_ORDERS)

    second = seed_all()
    assert second["work_orders"] == len(WORK_ORDERS)

    db = SessionLocal()
    try:
        seed_ids = [item["id"] for item in WORK_ORDERS]
        rows = db.query(WorkOrder).filter(WorkOrder.id.in_(seed_ids)).all()
        assert len(rows) == len(WORK_ORDERS)

        by_id = {row.id: row for row in rows}
        for item in WORK_ORDERS:
            wo = by_id[item["id"]]
            assert wo.asset_id == item["asset_id"]
            assert wo.status == "open"
            assert wo.assigned_technician_id is None
            asset = db.get(Asset, wo.asset_id)
            assert asset is not None
            assert wo.zone_id == asset.zone_id
            assert wo.utility == asset.utility_kind
    finally:
        db.close()
