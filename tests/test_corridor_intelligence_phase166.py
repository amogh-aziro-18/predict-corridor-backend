"""Phase 16.6 — corridor cross-utility dependency exposure (evidence only)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm.attributes import flag_modified

from app.db.seed import seed_all
from app.db.session import SessionLocal
from app.domain.models import Asset, AssetDependency
from app.domain.models.alert import Alert
from app.domain.models.incident import Incident
from app.domain.models.work_order import WorkOrder
from app.main import app
from app.services.dashboard_service import DashboardService


def _client() -> TestClient:
    return TestClient(app)


def _set_condition(db, asset_id: str, condition: str) -> None:
    asset = db.get(Asset, asset_id)
    assert asset is not None
    profile = dict(asset.profile or {})
    profile["condition"] = condition
    asset.profile = profile
    flag_modified(asset, "profile")
    if condition in {"critical", "unavailable"}:
        if condition == "unavailable":
            asset.status = "offline"
    else:
        asset.status = "operational"
    db.add(asset)
    db.commit()
    db.refresh(asset)


def _reset_feeders(db) -> None:
    for asset_id in ("FDR-A-01", "FDR-C-01", "WTP-A-01"):
        asset = db.get(Asset, asset_id)
        if asset is None:
            continue
        _set_condition(db, asset_id, "healthy")


def _ensure_edge(db, source: str, target: str) -> None:
    edges = (
        db.query(AssetDependency)
        .filter(
            AssetDependency.source_asset_id == source,
            AssetDependency.target_asset_id == target,
        )
        .all()
    )
    assert edges, f"Missing edge {source} → {target}"
    dirty = False
    for e in edges:
        if not e.enabled:
            e.enabled = True
            db.add(e)
            dirty = True
    if dirty:
        db.commit()


def test_healthy_corridor_no_fabricated_exposure() -> None:
    seed_all()
    db = SessionLocal()
    try:
        _reset_feeders(db)
        db.expire_all()
        body = DashboardService(db).corridor_dashboard()
        assert body.overall_posture in {
            "HEALTHY",
            "STABLE",
            "DEGRADED",
            "CRITICAL",
            "UNAVAILABLE",
        }
        exp = body.dependency_exposure
        assert exp is not None
        assert exp.available is False
        assert exp.sources == []
        assert "FDR-C-01" not in (body.situation_summary or "")
        assert "modeled downstream" not in (body.situation_summary or "").lower() or (
            "no modeled" in (body.situation_summary or "").lower()
        )
    finally:
        _reset_feeders(db)
        db.close()


def test_fdr_c_critical_water_dependency_on_corridor() -> None:
    seed_all()
    db = SessionLocal()
    try:
        _ensure_edge(db, "FDR-C-01", "PMP-C-01")
        _set_condition(db, "FDR-A-01", "healthy")
        _set_condition(db, "FDR-C-01", "critical")
        db.expire_all()
        body = DashboardService(db).corridor_dashboard()
        exp = body.dependency_exposure
        assert exp is not None
        assert exp.available is True
        assert exp.hypothetical is True
        assert exp.contributes_to_overall_posture is False
        assert "FDR-C-01" in exp.source_asset_ids
        assert "PMP-C-01" in exp.affected_asset_ids
        assert "water" in exp.affected_utilities
        assert "zone-c" in exp.affected_zones
        src = next(s for s in exp.sources if s.source_asset_id == "FDR-C-01")
        assert src.source_utility == "power"
        assert any(a.asset_id == "PMP-C-01" for a in src.affected_assets)
        assert src.dependency_paths
        summary = (body.situation_summary or "").lower()
        assert "power" in summary
        assert "pmp-c-01" in summary
        assert "water" in summary
        assert "potential" in summary or "hypothetical" in summary or "modeled" in summary
    finally:
        _reset_feeders(db)
        db.close()


def test_fdr_a_critical_water_and_gas_one_exposure() -> None:
    seed_all()
    db = SessionLocal()
    try:
        _ensure_edge(db, "FDR-A-01", "PMP-A-01")
        _ensure_edge(db, "FDR-A-01", "CMP-A-01")
        _set_condition(db, "FDR-C-01", "healthy")
        _set_condition(db, "FDR-A-01", "critical")
        db.expire_all()
        body = DashboardService(db).corridor_dashboard()
        exp = body.dependency_exposure
        assert exp is not None
        assert exp.available is True
        assert exp.source_asset_ids == ["FDR-A-01"]
        assert set(exp.affected_utilities) == {"gas", "water", "communications"}
        assert "PMP-A-01" in exp.affected_asset_ids
        assert "CMP-A-01" in exp.affected_asset_ids
        assert "POP-A-01" in exp.affected_asset_ids
        assert len(exp.sources) == 1
        summary = (body.situation_summary or "").lower()
        assert "water" in summary
        assert "gas" in summary
        assert "pmp-a-01" in summary
        assert "cmp-a-01" in summary
    finally:
        _reset_feeders(db)
        db.close()


def test_critical_no_dependents_no_fabricated_exposure() -> None:
    seed_all()
    db = SessionLocal()
    try:
        _reset_feeders(db)
        _set_condition(db, "WTP-A-01", "critical")
        db.expire_all()
        body = DashboardService(db).corridor_dashboard()
        exp = body.dependency_exposure
        assert exp is not None
        if exp.available:
            assert "WTP-A-01" not in exp.source_asset_ids
        else:
            assert exp.sources == []
    finally:
        _reset_feeders(db)
        db.close()


def test_existing_corridor_aggregation_fields_unchanged() -> None:
    seed_all()
    db = SessionLocal()
    try:
        _reset_feeders(db)
        body = DashboardService(db).corridor_dashboard()
        assert body.assessment_kind == "composed_utility_operational_posture"
        kinds = {u.utility for u in body.utilities}
        assert kinds == {"water", "power", "gas", "communications"}
        water = next(u for u in body.utilities if u.utility == "water")
        power = next(u for u in body.utilities if u.utility == "power")
        gas = next(u for u in body.utilities if u.utility == "gas")
        communications = next(
            u for u in body.utilities if u.utility == "communications"
        )
        assert water.overall_health is not None
        assert power.overall_condition is not None
        assert gas.overall_condition is not None
        assert communications.overall_condition is not None
        assert isinstance(body.total_assets, int)
        assert isinstance(body.total_active_alerts, int)
        assert isinstance(body.total_critical_assets, int)
        assert body.explanation
        assert body.drivers
    finally:
        _reset_feeders(db)
        db.close()


def test_dependency_evidence_does_not_change_corridor_posture() -> None:
    seed_all()
    db = SessionLocal()
    try:
        _ensure_edge(db, "FDR-C-01", "PMP-C-01")
        _set_condition(db, "FDR-C-01", "healthy")
        svc = DashboardService(db)
        before = svc.corridor_dashboard()
        posture_before = before.overall_posture
        assert before.dependency_exposure is None or before.dependency_exposure.available is False

        _set_condition(db, "FDR-C-01", "critical")
        db.expire_all()
        after = svc.corridor_dashboard()
        assert after.dependency_exposure is not None
        assert after.dependency_exposure.available is True
        # Adding dependency evidence alone must not change posture math.
        # Note: setting profile.condition critical may also affect Power dashboard
        # condition aggregation — isolate by comparing that contributes_to_overall
        # is False and that posture equals a recompute that ignores exposure.
        assert after.dependency_exposure.contributes_to_overall_posture is False
        # Recompose posture from utility snapshots only (same as compose_corridor_posture).
        from app.services.dashboard_service import compose_corridor_posture

        recomputed = compose_corridor_posture([u.posture for u in after.utilities])
        assert after.overall_posture == recomputed
        # If Power live condition did not flip solely from our profile write,
        # posture should match before; otherwise posture change is from utility
        # dashboard math, not from the exposure object.
        if before.utilities == after.utilities:
            assert after.overall_posture == posture_before
    finally:
        _reset_feeders(db)
        db.close()


def test_no_side_effects_wo_incident_alert() -> None:
    seed_all()
    db = SessionLocal()
    try:
        wo_before = db.query(WorkOrder).count()
        incident_before = db.query(Incident).count()
        alert_before = db.query(Alert).count()
        _ensure_edge(db, "FDR-C-01", "PMP-C-01")
        _set_condition(db, "FDR-C-01", "critical")
        body = DashboardService(db).corridor_dashboard()
        assert body.dependency_exposure is not None
        assert body.dependency_exposure.available is True
        db.expire_all()
        assert db.query(WorkOrder).count() == wo_before
        assert db.query(Incident).count() == incident_before
        assert db.query(Alert).count() == alert_before
    finally:
        _reset_feeders(db)
        db.close()


def test_situation_summary_deterministic_and_dynamic() -> None:
    seed_all()
    db = SessionLocal()
    try:
        _ensure_edge(db, "FDR-C-01", "PMP-C-01")
        _set_condition(db, "FDR-C-01", "critical")
        db.expire_all()
        svc = DashboardService(db)
        a = svc.corridor_dashboard()
        b = svc.corridor_dashboard()
        assert a.situation_summary == b.situation_summary
        assert a.situation_summary
        # Summary is data-driven (mentions live source/dependent), not a blank stub.
        text = a.situation_summary.lower()
        assert "fdr-c-01" in text or "power" in text
        assert "pmp-c-01" in text or "water" in text
        # Code must not ship a single hardcoded canned sentence for only FDR-C.
        # Verify FDR-A path produces a different dynamic summary.
        _set_condition(db, "FDR-C-01", "healthy")
        _ensure_edge(db, "FDR-A-01", "PMP-A-01")
        _ensure_edge(db, "FDR-A-01", "CMP-A-01")
        _set_condition(db, "FDR-A-01", "critical")
        db.expire_all()
        c = svc.corridor_dashboard()
        assert c.situation_summary
        assert c.situation_summary != a.situation_summary
        assert "gas" in c.situation_summary.lower()
    finally:
        _reset_feeders(db)
        db.close()


def test_api_corridor_exposes_dependency_fields() -> None:
    seed_all()
    db = SessionLocal()
    try:
        _ensure_edge(db, "FDR-C-01", "PMP-C-01")
        _set_condition(db, "FDR-C-01", "critical")
        res = _client().get("/api/dashboard/corridor")
        assert res.status_code == 200, res.text
        body = res.json()
        assert "dependency_exposure" in body
        assert "situation_summary" in body
        exp = body["dependency_exposure"]
        assert exp["available"] is True
        assert "PMP-C-01" in exp["affected_asset_ids"]
        assert exp["contributes_to_overall_posture"] is False
        assert body["assessment_kind"] == "composed_utility_operational_posture"
        assert {u["utility"] for u in body["utilities"]} == {
            "water",
            "power",
            "gas",
            "communications",
        }
    finally:
        _reset_feeders(db)
        db.close()
