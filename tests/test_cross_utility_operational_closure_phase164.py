"""Phase 16.4 — cross-utility operational closure (rec → WO → tech → lifecycle)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm.attributes import flag_modified

from app.db.seed import seed_all
from app.db.session import SessionLocal
from app.domain.models import Asset, WorkOrder
from app.main import app
from app.services.operational_intelligence_service import OperationalIntelligenceService
from app.services.recommendation_service import RecommendationService


def _client() -> TestClient:
    return TestClient(app)


def _set_profile_condition(db, asset_id: str, condition: str) -> None:
    asset = db.get(Asset, asset_id)
    assert asset is not None
    profile = dict(asset.profile or {})
    profile["condition"] = condition
    asset.profile = profile
    flag_modified(asset, "profile")
    db.add(asset)
    db.commit()
    db.refresh(asset)


def _ensure_edge(db, source: str, target: str) -> None:
    from app.domain.models import AssetDependency

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


def _xu_rec(recs: list, asset_id: str):
    hits = [
        r
        for r in recs
        if getattr(r, "source", None) == "cross_utility"
        and getattr(r, "affected_asset", None) == asset_id
    ]
    assert len(hits) == 1, f"Expected one cross_utility rec for {asset_id}, got {len(hits)}"
    return hits[0]


def _wo_payload_from_rec(rec) -> dict:
    """Mirror FE recommendationToWorkOrderPayload (source asset only)."""
    snap = {
        "id": rec.id,
        "priority": rec.priority,
        "severity": rec.severity,
        "title": rec.title,
        "description": rec.description,
        "affected_asset": rec.affected_asset,
        "affected_zone": rec.affected_zone,
        "recommendation": rec.recommendation,
        "estimated_business_impact": rec.estimated_business_impact,
        "confidence": rec.confidence,
        "generated_at": rec.generated_at.isoformat()
        if hasattr(rec.generated_at, "isoformat")
        else str(rec.generated_at),
        "rule_code": rec.rule_code,
        "scenario_key": rec.scenario_key,
        "source": rec.source,
    }
    if getattr(rec, "context", None) is not None:
        snap["context"] = rec.context
    return {
        "asset_id": rec.affected_asset,
        "title": rec.title,
        "description": rec.description,
        "priority": rec.priority,
        "source": "recommendation",
        "recommendation_id": rec.id,
        "recommendation_snapshot": snap,
        "created_by": "phase-16.4-test",
    }


def _delete_wo(wo_id: str) -> None:
    db = SessionLocal()
    try:
        row = db.get(WorkOrder, wo_id)
        if row is not None:
            db.delete(row)
            db.commit()
    finally:
        db.close()


def test_fdr_c_cross_utility_wo_on_source_power() -> None:
    seed_all()
    client = _client()
    db = SessionLocal()
    wo_id: str | None = None
    try:
        _ensure_edge(db, "FDR-C-01", "PMP-C-01")
        _set_profile_condition(db, "FDR-C-01", "critical")
        recs = RecommendationService(db).list_recommendations()
        rec = _xu_rec(recs, "FDR-C-01")
        assert rec.rule_code == "cross_utility_dependency_impact"
        assert rec.context is not None
        assert rec.context["source_asset"]["asset_id"] == "FDR-C-01"
        assert rec.context["source_asset"]["utility"] == "power"

        payload = _wo_payload_from_rec(rec)
        created = client.post("/api/work-orders", json=payload)
        assert created.status_code == 201, created.text
        body = created.json()
        wo_id = body["id"]
        assert body["asset_id"] == "FDR-C-01"
        assert body["utility"] == "power"
        assert body["zone_id"] == "zone-c"
        assert body["recommendation_id"] == rec.id
        assert body["source"] == "recommendation"
        snap = body["recommendation_snapshot"]
        assert snap["source"] == "cross_utility"
        assert snap["rule_code"] == "cross_utility_dependency_impact"
        assert snap["context"]["source_asset"]["asset_id"] == "FDR-C-01"
        assert snap["context"]["source_asset"]["condition"] == "critical"
        affected_ids = {a["asset_id"] for a in snap["context"]["impact"]["affected_assets"]}
        assert affected_ids == {"PMP-C-01"}
        assert "water" in snap["context"]["impact"]["affected_utilities"]
        assert "zone-c" in snap["context"]["impact"]["affected_zones"]
        assert snap["context"]["impact"]["hypothetical"] is True
        assert snap["context"]["impact"]["dependency_paths"]
    finally:
        if wo_id:
            _delete_wo(wo_id)
        db.close()


def test_duplicate_active_cross_utility_wo_rejected() -> None:
    seed_all()
    client = _client()
    db = SessionLocal()
    wo_id: str | None = None
    try:
        _ensure_edge(db, "FDR-C-01", "PMP-C-01")
        _set_profile_condition(db, "FDR-C-01", "critical")
        rec = _xu_rec(RecommendationService(db).list_recommendations(), "FDR-C-01")
        payload = _wo_payload_from_rec(rec)
        first = client.post("/api/work-orders", json=payload)
        assert first.status_code == 201, first.text
        wo_id = first.json()["id"]
        dup = client.post("/api/work-orders", json=payload)
        assert dup.status_code == 422
        assert "Active work order already exists" in dup.json()["detail"]
    finally:
        if wo_id:
            _delete_wo(wo_id)
        db.close()


def test_power_tech_assign_water_tech_rejected_lifecycle_snapshot_intact() -> None:
    seed_all()
    client = _client()
    db = SessionLocal()
    wo_id: str | None = None
    try:
        _ensure_edge(db, "FDR-C-01", "PMP-C-01")
        _set_profile_condition(db, "FDR-C-01", "critical")
        rec = _xu_rec(RecommendationService(db).list_recommendations(), "FDR-C-01")
        created = client.post("/api/work-orders", json=_wo_payload_from_rec(rec))
        assert created.status_code == 201, created.text
        wo_id = created.json()["id"]
        snap_open = created.json()["recommendation_snapshot"]

        # Water-only technician rejected for Power WO.
        bad = client.patch(
            f"/api/work-orders/{wo_id}/status",
            json={
                "status": "assigned",
                "assigned_technician_id": "TEC-A-01",
                "actor": "phase-16.4-test",
            },
        )
        assert bad.status_code in {400, 422}
        detail = str(bad.json().get("detail", "")).lower()
        assert "not qualified" in detail or "qualified" in detail

        # Power-qualified technician accepted.
        assigned = client.patch(
            f"/api/work-orders/{wo_id}/status",
            json={
                "status": "assigned",
                "assigned_technician_id": "TEC-P-02",
                "actor": "phase-16.4-test",
            },
        )
        assert assigned.status_code == 200, assigned.text
        assert assigned.json()["assigned_technician_id"] == "TEC-P-02"
        assert assigned.json()["status"] == "assigned"

        for status in ("in_progress", "completed", "closed"):
            res = client.patch(
                f"/api/work-orders/{wo_id}/status",
                json={"status": status, "actor": "phase-16.4-test"},
            )
            assert res.status_code == 200, res.text
            assert res.json()["status"] == status

        final = client.get(f"/api/work-orders/{wo_id}")
        assert final.status_code == 200
        body = final.json()
        assert body["status"] == "closed"
        assert body["utility"] == "power"
        assert body["asset_id"] == "FDR-C-01"
        # Snapshot intact after closure.
        assert body["recommendation_snapshot"] == snap_open
        assert body["recommendation_snapshot"]["source"] == "cross_utility"
        assert body["recommendation_snapshot"]["context"]["impact"]["hypothetical"] is True

        events = body.get("events") or []
        to_statuses = {e["to_status"] for e in events}
        assert {"assigned", "in_progress", "completed", "closed"}.issubset(to_statuses)
        assert len(events) >= 4
    finally:
        if wo_id:
            _delete_wo(wo_id)
        db.close()


def test_fdr_a_multi_utility_one_power_source_wo() -> None:
    seed_all()
    client = _client()
    db = SessionLocal()
    wo_id: str | None = None
    try:
        _ensure_edge(db, "FDR-A-01", "PMP-A-01")
        _ensure_edge(db, "FDR-A-01", "CMP-A-01")
        _set_profile_condition(db, "FDR-A-01", "critical")
        recs = RecommendationService(db).list_recommendations()
        xu = [
            r
            for r in recs
            if r.source == "cross_utility" and r.affected_asset == "FDR-A-01"
        ]
        assert len(xu) == 1
        rec = xu[0]
        affected = {a["asset_id"] for a in rec.context["impact"]["affected_assets"]}
        assert affected == {"PMP-A-01", "CMP-A-01", "POP-A-01"}
        assert set(rec.context["impact"]["affected_utilities"]) == {
            "gas",
            "water",
            "communications",
        }

        created = client.post("/api/work-orders", json=_wo_payload_from_rec(rec))
        assert created.status_code == 201, created.text
        wo_id = created.json()["id"]
        body = created.json()
        assert body["asset_id"] == "FDR-A-01"
        assert body["utility"] == "power"
        # Only one WO for this recommendation — no auto WOs for dependents.
        open_wos = client.get("/api/operations/work-orders").json()
        related = [w for w in open_wos if w.get("recommendation_id") == rec.id]
        assert len(related) == 1
        assert related[0]["asset_id"] == "FDR-A-01"
        assert not any(
            w["asset_id"] in {"PMP-A-01", "CMP-A-01", "POP-A-01"}
            and w.get("recommendation_id") == rec.id
            for w in open_wos
        )
        snap = body["recommendation_snapshot"]
        assert {a["asset_id"] for a in snap["context"]["impact"]["affected_assets"]} == {
            "PMP-A-01",
            "CMP-A-01",
            "POP-A-01",
        }
    finally:
        if wo_id:
            _delete_wo(wo_id)
        db.close()


def test_recommendation_generation_does_not_create_work_order() -> None:
    seed_all()
    db = SessionLocal()
    try:
        before = db.query(WorkOrder).count()
        _ensure_edge(db, "FDR-C-01", "PMP-C-01")
        _set_profile_condition(db, "FDR-C-01", "critical")
        recs = RecommendationService(db).list_recommendations()
        assert any(r.source == "cross_utility" and r.affected_asset == "FDR-C-01" for r in recs)
        db.expire_all()
        assert db.query(WorkOrder).count() == before
        # Direct OIS path also creates no WO.
        drafts = OperationalIntelligenceService(db).cross_utility_dependency_rules()
        assert any(d.affected_asset == "FDR-C-01" for d in drafts)
        db.expire_all()
        assert db.query(WorkOrder).count() == before
    finally:
        db.close()
