"""Phase 16.2 — cross-utility operational recommendations via existing OIS."""

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


def _ensure_supplies_edge(db, *, source_asset_id: str, target_asset_id: str) -> None:
    """Guard against shared-DB pollution leaving POC edges disabled."""
    edges = (
        db.query(AssetDependency)
        .filter(
            AssetDependency.source_asset_id == source_asset_id,
            AssetDependency.target_asset_id == target_asset_id,
        )
        .all()
    )
    assert edges, f"Missing supplies edge {source_asset_id} → {target_asset_id}"
    dirty = False
    for edge in edges:
        if not edge.enabled:
            edge.enabled = True
            db.add(edge)
            dirty = True
    if dirty:
        db.commit()


def _cross_utility_recs(recs: list) -> list:
    return [r for r in recs if getattr(r, "source", None) == "cross_utility"]


def test_critical_power_to_water_recommendation() -> None:
    seed_all()
    db = SessionLocal()
    try:
        _ensure_supplies_edge(db, source_asset_id="FDR-C-01", target_asset_id="PMP-C-01")
        _set_profile_condition(db, "FDR-C-01", "critical")
        recs = RecommendationService(db).list_recommendations()
        xu = _cross_utility_recs(recs)
        assert len(xu) >= 1
        hit = next(r for r in xu if r.affected_asset == "FDR-C-01")
        assert hit.source == "cross_utility"
        assert hit.rule_code == "cross_utility_dependency_impact"
        assert hit.affected_asset == "FDR-C-01"
        assert hit.affected_zone == "zone-c"
        assert hit.priority == "critical"
        text = f"{hit.description} {hit.recommendation}".lower()
        assert "pmp-c-01" in text
        assert "water" in text
        assert "may affect" in text or "potential" in text
        assert "will fail" not in text
        assert "guaranteed" not in text
        assert "service outage" not in text
    finally:
        db.close()


def test_power_to_water_gas_and_communications_single_recommendation() -> None:
    seed_all()
    db = SessionLocal()
    try:
        _ensure_supplies_edge(db, source_asset_id="FDR-A-01", target_asset_id="PMP-A-01")
        _ensure_supplies_edge(db, source_asset_id="FDR-A-01", target_asset_id="CMP-A-01")
        _ensure_supplies_edge(db, source_asset_id="FDR-A-01", target_asset_id="POP-A-01")
        _set_profile_condition(db, "FDR-A-01", "critical")
        recs = RecommendationService(db).list_recommendations()
        xu = [r for r in _cross_utility_recs(recs) if r.affected_asset == "FDR-A-01"]
        assert len(xu) == 1
        hit = xu[0]
        text = f"{hit.description} {hit.recommendation}".lower()
        assert "pmp-a-01" in text
        assert "cmp-a-01" in text
        assert "pop-a-01" in text
        assert "water" in text
        assert "gas" in text
        assert "communications" in text
        ctx = getattr(hit, "context", None) or {}
        impact = ctx.get("impact") if isinstance(ctx, dict) else None
        if isinstance(impact, dict):
            assert impact.get("hypothetical") is True
            utils = {str(u).lower() for u in (impact.get("affected_utilities") or [])}
            assert "communications" in utils
    finally:
        db.close()


def test_no_dependency_no_cross_utility_recommendation() -> None:
    seed_all()
    db = SessionLocal()
    try:
        # WTP-A-01 has no outgoing supplies edges (Phase 16.1 coverage).
        _set_profile_condition(db, "WTP-A-01", "critical")
        recs = RecommendationService(db).list_recommendations()
        xu = [r for r in _cross_utility_recs(recs) if r.affected_asset == "WTP-A-01"]
        assert xu == []
    finally:
        db.close()


def test_non_critical_condition_no_cross_utility_recommendation() -> None:
    seed_all()
    db = SessionLocal()
    try:
        _set_profile_condition(db, "FDR-C-01", "warning")
        recs = RecommendationService(db).list_recommendations()
        xu = [r for r in _cross_utility_recs(recs) if r.affected_asset == "FDR-C-01"]
        assert xu == []

        _set_profile_condition(db, "FDR-C-01", "high_risk")
        recs = RecommendationService(db).list_recommendations()
        xu = [r for r in _cross_utility_recs(recs) if r.affected_asset == "FDR-C-01"]
        assert xu == []

        _set_profile_condition(db, "FDR-C-01", "healthy")
        recs = RecommendationService(db).list_recommendations()
        xu = [r for r in _cross_utility_recs(recs) if r.affected_asset == "FDR-C-01"]
        assert xu == []
    finally:
        db.close()


def test_unavailable_trigger_produces_recommendation() -> None:
    seed_all()
    db = SessionLocal()
    try:
        _set_profile_condition(db, "FDR-C-01", "unavailable")
        recs = RecommendationService(db).list_recommendations()
        xu = [r for r in _cross_utility_recs(recs) if r.affected_asset == "FDR-C-01"]
        assert len(xu) == 1
        assert "unavailable" in xu[0].description.lower()
    finally:
        db.close()


def test_stable_recommendation_id() -> None:
    seed_all()
    db = SessionLocal()
    try:
        _set_profile_condition(db, "FDR-C-01", "critical")
        a = RecommendationService(db).list_recommendations()
        b = RecommendationService(db).list_recommendations()
        id_a = next(r.id for r in a if r.source == "cross_utility" and r.affected_asset == "FDR-C-01")
        id_b = next(r.id for r in b if r.source == "cross_utility" and r.affected_asset == "FDR-C-01")
        assert id_a == id_b
        expected = OperationalIntelligenceService.stable_id(
            "cross_utility_dependency_impact", "FDR-C-01"
        )
        assert id_a == expected
        assert id_a.startswith("rec-")
        assert len(id_a) == 16  # rec- + 12 hex
    finally:
        db.close()


def test_human_in_the_loop_no_side_effects() -> None:
    seed_all()
    db = SessionLocal()
    try:
        tel_alerts = db.query(Alert).count()
        wo_before = db.query(WorkOrder).count()
        incident_before = db.query(Incident).count()
        tech_assigned = (
            db.query(WorkOrder)
            .filter(WorkOrder.assigned_technician_id.isnot(None))
            .count()
        )

        _set_profile_condition(db, "FDR-A-01", "critical")
        recs = RecommendationService(db).list_recommendations()
        assert any(r.source == "cross_utility" and r.affected_asset == "FDR-A-01" for r in recs)

        db.expire_all()
        assert db.query(Alert).count() == tel_alerts
        assert db.query(WorkOrder).count() == wo_before
        assert db.query(Incident).count() == incident_before
        assert (
            db.query(WorkOrder)
            .filter(WorkOrder.assigned_technician_id.isnot(None))
            .count()
            == tech_assigned
        )
    finally:
        db.close()


def test_recommendations_api_includes_cross_utility() -> None:
    seed_all()
    db = SessionLocal()
    try:
        _set_profile_condition(db, "FDR-C-01", "critical")
    finally:
        db.close()

    client = _client()
    res = client.get("/api/recommendations")
    assert res.status_code == 200, res.text
    body = res.json()
    xu = [r for r in body if r.get("source") == "cross_utility" and r.get("affected_asset") == "FDR-C-01"]
    assert len(xu) == 1
    assert xu[0]["rule_code"] == "cross_utility_dependency_impact"


def test_ois_consumes_impact_not_duplicate_graph() -> None:
    """Direct OIS call still yields one draft; disabled edge suppresses it."""
    seed_all()
    db = SessionLocal()
    try:
        _ensure_supplies_edge(db, source_asset_id="FDR-C-01", target_asset_id="PMP-C-01")
        _set_profile_condition(db, "FDR-C-01", "critical")
        drafts = OperationalIntelligenceService(db).cross_utility_dependency_rules()
        hits = [d for d in drafts if d.affected_asset == "FDR-C-01"]
        assert len(hits) == 1
        assert hits[0].rule_code == "cross_utility_dependency_impact"

        # Disable the FDR-C → PMP-C edge — no dependents → no draft.
        edges = (
            db.query(AssetDependency)
            .filter(
                AssetDependency.source_asset_id == "FDR-C-01",
                AssetDependency.target_asset_id == "PMP-C-01",
            )
            .all()
        )
        assert edges
        for e in edges:
            e.enabled = False
            db.add(e)
        db.commit()

        drafts2 = OperationalIntelligenceService(db).cross_utility_dependency_rules()
        assert [d for d in drafts2 if d.affected_asset == "FDR-C-01"] == []
    finally:
        db.close()
        # Re-seed restores POC edges enabled (shared DB safety).
        seed_all()
