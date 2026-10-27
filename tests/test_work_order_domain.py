"""Work order domain persistence tests (Phase 7A)."""

from __future__ import annotations

import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.domain.enums import (
    TechnicianAvailability,
    WorkOrderSource,
    WorkOrderStatus,
)
from app.domain.models import Technician, WorkOrder, WorkOrderEvent
from app.domain.repositories import (
    AssetRepository,
    TechnicianRepository,
    WorkOrderEventRepository,
    WorkOrderRepository,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def db() -> Session:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _pick_asset(db: Session):
    assets = AssetRepository(db).list(utility_kind="water", asset_type="pump_station")
    if not assets:
        assets = AssetRepository(db).list(utility_kind="water")
    if not assets:
        pytest.skip("No seeded assets in database")
    return assets[0]


def test_work_order_persists_with_asset_reference(db: Session) -> None:
    asset = _pick_asset(db)
    wo_id = _unique("WO-TEST")
    repo = WorkOrderRepository(db)

    work_order = WorkOrder(
        id=wo_id,
        asset_id=asset.id,
        utility=asset.utility_kind,
        zone_id=asset.zone_id,
        title="Inspect pump telemetry anomaly",
        priority="high",
        status=WorkOrderStatus.OPEN.value,
        source=WorkOrderSource.MANUAL.value,
    )
    repo.save(work_order)

    loaded = repo.get(wo_id)
    assert loaded is not None
    assert loaded.asset_id == asset.id
    assert loaded.utility == asset.utility_kind
    assert loaded.zone_id == asset.zone_id
    assert loaded.started_at is None
    assert loaded.completed_at is None

    repo.delete(loaded)


def test_technician_persists(db: Session) -> None:
    asset = _pick_asset(db)
    tech_id = _unique("TEC-TEST")
    repo = TechnicianRepository(db)

    technician = Technician(
        id=tech_id,
        name="Test Technician",
        role="Field Engineer",
        primary_zone_id=asset.zone_id,
        skills=["hydraulics", "electrical"],
        utility_kinds=["water"],
        availability=TechnicianAvailability.AVAILABLE.value,
        emergency_certified=True,
    )
    repo.save(technician)

    loaded = repo.get(tech_id)
    assert loaded is not None
    assert loaded.skills == ["hydraulics", "electrical"]
    assert loaded.emergency_certified is True

    repo.delete(loaded)


def test_work_order_event_persists(db: Session) -> None:
    asset = _pick_asset(db)
    wo_id = _unique("WO-TEST")
    event_id = _unique("WOE-TEST")
    wo_repo = WorkOrderRepository(db)
    event_repo = WorkOrderEventRepository(db)

    work_order = WorkOrder(
        id=wo_id,
        asset_id=asset.id,
        utility=asset.utility_kind,
        zone_id=asset.zone_id,
        title="Event persistence check",
        source=WorkOrderSource.MANUAL.value,
    )
    wo_repo.save(work_order)

    event = WorkOrderEvent(
        id=event_id,
        work_order_id=wo_id,
        from_status=None,
        to_status=WorkOrderStatus.OPEN.value,
        actor="test-suite",
        notes="Created for audit trail test",
    )
    event_repo.save(event)

    events = event_repo.list_for_work_order(wo_id)
    assert len(events) == 1
    assert events[0].to_status == WorkOrderStatus.OPEN.value
    assert events[0].notes == "Created for audit trail test"

    wo_repo.delete(work_order)


def test_recommendation_snapshot_jsonb(db: Session) -> None:
    asset = _pick_asset(db)
    wo_id = _unique("WO-TEST")
    snapshot = {
        "affected_asset": asset.id,
        "affected_zone": asset.zone_id,
        "priority": "critical",
        "title": "Pump failure contingency",
        "description": "Pressure degrading",
        "recommendation": "Activate standby pump",
        "confidence": 0.96,
        "rule_code": "PUMP_FAILURE",
        "scenario_key": "pump_failure",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    repo = WorkOrderRepository(db)
    work_order = WorkOrder(
        id=wo_id,
        asset_id=asset.id,
        utility=asset.utility_kind,
        zone_id=asset.zone_id,
        recommendation_id="rec-test-001",
        recommendation_snapshot=snapshot,
        title=snapshot["title"],
        description=snapshot["description"],
        priority="critical",
        source=WorkOrderSource.RECOMMENDATION.value,
    )
    repo.save(work_order)

    loaded = repo.get(wo_id)
    assert loaded is not None
    assert loaded.recommendation_snapshot == snapshot
    assert loaded.recommendation_snapshot["rule_code"] == "PUMP_FAILURE"

    repo.delete(loaded)


def test_nullable_lifecycle_timestamps(db: Session) -> None:
    asset = _pick_asset(db)
    wo_id = _unique("WO-TEST")
    repo = WorkOrderRepository(db)
    due = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

    work_order = WorkOrder(
        id=wo_id,
        asset_id=asset.id,
        utility=asset.utility_kind,
        zone_id=asset.zone_id,
        title="Lifecycle timestamp test",
        due_at=due,
        source=WorkOrderSource.MANUAL.value,
    )
    repo.save(work_order)

    loaded = repo.get(wo_id)
    assert loaded is not None
    assert loaded.due_at == due
    assert loaded.started_at is None
    assert loaded.completed_at is None
    assert loaded.verified_at is None
    assert loaded.closed_at is None
    assert loaded.completion_notes is None
    assert loaded.verification_notes is None

    repo.delete(loaded)


def test_work_order_technician_relationship(db: Session) -> None:
    asset = _pick_asset(db)
    wo_id = _unique("WO-TEST")
    tech_id = _unique("TEC-TEST")
    tech_repo = TechnicianRepository(db)
    wo_repo = WorkOrderRepository(db)

    technician = Technician(
        id=tech_id,
        name="Assigned Tech",
        role="Hydraulics",
        primary_zone_id=asset.zone_id,
        utility_kinds=["water"],
        availability=TechnicianAvailability.ON_TASK.value,
    )
    tech_repo.save(technician)

    work_order = WorkOrder(
        id=wo_id,
        asset_id=asset.id,
        utility=asset.utility_kind,
        zone_id=asset.zone_id,
        title="Assignment relationship test",
        assigned_technician_id=tech_id,
        status=WorkOrderStatus.ASSIGNED.value,
        source=WorkOrderSource.MANUAL.value,
    )
    wo_repo.save(work_order)

    loaded = wo_repo.get(wo_id)
    assert loaded is not None
    assert loaded.assigned_technician is not None
    assert loaded.assigned_technician.id == tech_id
    assert loaded.assigned_technician.name == "Assigned Tech"
    assert len(loaded.assigned_technician.assigned_work_orders) >= 1

    wo_repo.delete(loaded)
    tech_repo.delete(technician)


def test_work_order_event_relationship(db: Session) -> None:
    asset = _pick_asset(db)
    wo_id = _unique("WO-TEST")
    event_id = _unique("WOE-TEST")
    wo_repo = WorkOrderRepository(db)

    work_order = WorkOrder(
        id=wo_id,
        asset_id=asset.id,
        utility=asset.utility_kind,
        zone_id=asset.zone_id,
        title="Event relationship test",
        source=WorkOrderSource.MANUAL.value,
    )
    wo_repo.add(work_order)
    work_order.events.append(
        WorkOrderEvent(
            id=event_id,
            from_status=None,
            to_status=WorkOrderStatus.OPEN.value,
            actor="test-suite",
        )
    )
    wo_repo.save(work_order)

    loaded = wo_repo.get(wo_id)
    assert loaded is not None
    assert len(loaded.events) == 1
    assert loaded.events[0].work_order_id == wo_id

    wo_repo.delete(loaded)


def test_invalid_asset_reference_rejected(db: Session) -> None:
    wo_id = _unique("WO-TEST")
    work_order = WorkOrder(
        id=wo_id,
        asset_id="DOES-NOT-EXIST-XYZ",
        utility="water",
        zone_id="zone-a",
        title="Invalid asset FK test",
        source=WorkOrderSource.MANUAL.value,
    )
    db.add(work_order)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_migration_upgrade_and_downgrade() -> None:
    python = sys.executable
    result = subprocess.run(
        [python, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout

    current = subprocess.run(
        [python, "-m", "alembic", "current"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "0009_asset_dependencies" in current.stdout

    downgrade = subprocess.run(
        [python, "-m", "alembic", "downgrade", "0004_zone_scenarios"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert downgrade.returncode == 0, downgrade.stderr or downgrade.stdout

    upgrade_again = subprocess.run(
        [python, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert upgrade_again.returncode == 0, upgrade_again.stderr or upgrade_again.stdout
