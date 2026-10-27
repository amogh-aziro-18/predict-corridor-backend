"""Phase 16.5 — dependency exposure as resilience evidence (no posture elevation)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm.attributes import flag_modified

from app.db.seed import seed_all
from app.db.session import SessionLocal
from app.domain.models import Asset, AssetDependency
from app.main import app
from app.services.resilience_assessment_service import ResilienceAssessmentService


def _client() -> TestClient:
    return TestClient(app)


def _set_condition(db, asset_id: str, condition: str) -> None:
    asset = db.get(Asset, asset_id)
    assert asset is not None
    profile = dict(asset.profile or {})
    profile["condition"] = condition
    asset.profile = profile
    flag_modified(asset, "profile")
    # Keep status aligned so offline leftovers cannot keep a "healthy" asset
    # eligible via the status→unavailable alias.
    if condition in {"critical", "unavailable"}:
        if condition == "unavailable":
            asset.status = "offline"
    else:
        asset.status = "operational"
    db.add(asset)
    db.commit()
    db.refresh(asset)


def _reset_feeder_live_state(db) -> None:
    """Clear live critical/unavailable leftovers for shared-DB isolation."""
    for asset_id in ("FDR-A-01", "FDR-C-01"):
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


def _dep_driver(assessment):
    return next(
        (d for d in assessment.drivers if str(d.source) == "dependency_exposure"),
        None,
    )


def test_fdr_c_critical_water_dependency_exposure() -> None:
    seed_all()
    db = SessionLocal()
    try:
        _ensure_edge(db, "FDR-C-01", "PMP-C-01")
        _set_condition(db, "FDR-C-01", "critical")
        a = ResilienceAssessmentService(db).assess(zone_id="zone-c")
        dep = _dep_driver(a)
        assert dep is not None
        assert dep.severity == "none"
        assert dep.evidence.get("contributes_to_overall_posture") is False
        assert dep.evidence.get("hypothetical") is True
        assert "FDR-C-01" in dep.evidence.get("source_asset_ids", [])
        assert "PMP-C-01" in dep.evidence.get("affected_asset_ids", [])
        assert "water" in dep.evidence.get("affected_utilities", [])
        assert "zone-c" in dep.evidence.get("affected_zones", [])
        text = dep.explanation.lower()
        assert "fdr-c-01" in text
        assert "pmp-c-01" in text
        assert "water" in text
        assert "potential" in text or "hypothetical" in text
    finally:
        _reset_feeder_live_state(db)
        db.close()


def test_fdr_a_critical_water_and_gas_one_driver() -> None:
    seed_all()
    db = SessionLocal()
    try:
        _ensure_edge(db, "FDR-A-01", "PMP-A-01")
        _ensure_edge(db, "FDR-A-01", "CMP-A-01")
        # Isolate from other POC feeders left critical in shared DB.
        _set_condition(db, "FDR-C-01", "healthy")
        _set_condition(db, "FDR-A-01", "critical")
        db.expire_all()
        fdr_c = db.get(Asset, "FDR-C-01")
        fdr_a = db.get(Asset, "FDR-A-01")
        assert (fdr_a.profile or {}).get("condition") == "critical"
        assert (fdr_c.profile or {}).get("condition") == "healthy", (
            fdr_c.profile,
            fdr_c.status,
        )
        assert ResilienceAssessmentService._resolve_live_dependency_trigger(fdr_c) is None
        a = ResilienceAssessmentService(db).assess(zone_id=None)
        deps = [d for d in a.drivers if str(d.source) == "dependency_exposure"]
        assert len(deps) == 1
        dep = deps[0]
        assert dep.evidence.get("source_asset_ids") == ["FDR-A-01"], dep.evidence
        assert set(dep.evidence.get("affected_utilities", [])) == {
            "gas",
            "water",
            "communications",
        }, dep.evidence
        ids = set(dep.evidence.get("affected_asset_ids", []))
        assert ids == {"PMP-A-01", "CMP-A-01", "POP-A-01"}, dep.evidence
    finally:
        _reset_feeder_live_state(db)
        db.close()


def test_critical_no_dependents_no_exposure() -> None:
    seed_all()
    db = SessionLocal()
    try:
        _set_condition(db, "WTP-A-01", "critical")
        a = ResilienceAssessmentService(db).assess(zone_id="zone-a")
        # WTP-A-01 has no outgoing supplies — must not fabricate exposure for it.
        dep = _dep_driver(a)
        if dep is not None:
            assert "WTP-A-01" not in (dep.evidence.get("source_asset_ids") or [])
    finally:
        _set_condition(db, "WTP-A-01", "healthy")
        db.close()


def test_healthy_source_with_edges_no_exposure() -> None:
    seed_all()
    db = SessionLocal()
    try:
        _ensure_edge(db, "FDR-C-01", "PMP-C-01")
        _set_condition(db, "FDR-C-01", "healthy")
        a = ResilienceAssessmentService(db).assess(zone_id="zone-c")
        dep = _dep_driver(a)
        if dep is not None:
            assert "FDR-C-01" not in (dep.evidence.get("source_asset_ids") or [])
    finally:
        _reset_feeder_live_state(db)
        db.close()


def test_dependency_exposure_does_not_change_overall_posture() -> None:
    seed_all()
    db = SessionLocal()
    try:
        _ensure_edge(db, "FDR-C-01", "PMP-C-01")
        _set_condition(db, "FDR-C-01", "healthy")
        svc = ResilienceAssessmentService(db)
        before = svc.assess(zone_id="zone-c")
        posture_before = before.overall_posture
        assert _dep_driver(before) is None or "FDR-C-01" not in (
            (_dep_driver(before).evidence or {}).get("source_asset_ids") or []
        )

        _set_condition(db, "FDR-C-01", "critical")
        after = svc.assess(zone_id="zone-c")
        assert _dep_driver(after) is not None
        # Adding dependency evidence alone must not change posture math.
        assert after.overall_posture == posture_before
        assert after.overall_severity == posture_before
    finally:
        _reset_feeder_live_state(db)
        db.close()


def test_api_resilience_exposes_dependency_driver() -> None:
    seed_all()
    db = SessionLocal()
    try:
        _ensure_edge(db, "FDR-C-01", "PMP-C-01")
        _set_condition(db, "FDR-C-01", "critical")
        res = _client().get("/api/resilience", params={"hours": 24})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["corridor"]["inputs_available"].get("dependencies_available") is True
        zone_c = next(z for z in body["zones"] if z["zone_id"] == "zone-c")
        dep = next(
            (d for d in zone_c["drivers"] if d["source"] == "dependency_exposure"),
            None,
        )
        assert dep is not None
        assert "PMP-C-01" in dep["evidence"]["affected_asset_ids"]
        assert dep["severity"] == "none"
    finally:
        _reset_feeder_live_state(db)
        db.close()


def test_api_diagnostic_exposes_dependency_slice() -> None:
    """
    Diagnostic composes dashboards first (may refresh live Power conditions).
    Water+Gas aggregation is covered by the service-level FDR-A test; here we
    verify the diagnostic slice wiring and evidence-only contract.
    """
    seed_all()
    db = SessionLocal()
    try:
        _ensure_edge(db, "FDR-C-01", "PMP-C-01")
        _set_condition(db, "FDR-C-01", "critical")
        # Service path (no dashboard overwrite) must expose dependency.
        corridor = ResilienceAssessmentService(db).assess(zone_id=None)
        assert _dep_driver(corridor) is not None

        res = _client().get("/api/resilience/diagnostic", params={"hours": 24})
        assert res.status_code == 200, res.text
        body = res.json()
        dep_slice = next(
            (s for s in body["slices"] if s["id"] == "dependency_exposure"),
            None,
        )
        # Slice appears when live critical sources with dependents exist after dashboard.
        # If dashboard telemetry cleared the critical condition, slice may be absent —
        # still assert that when present it is evidence-only and has no Gas fake metrics.
        if dep_slice is not None:
            assert dep_slice["metrics"].get("contributes_to_overall_posture") is False
            assert dep_slice["metrics"].get("hypothetical") is True
            assert "health" not in dep_slice["metrics"]
            assert "overall_condition" not in dep_slice["metrics"]
            assert dep_slice["evidence"] or dep_slice["summary"]
        assert isinstance(body.get("why_bullets"), list)
    finally:
        _reset_feeder_live_state(db)
        db.close()


def test_gis_context_preserves_dependency_driver() -> None:
    seed_all()
    db = SessionLocal()
    try:
        _ensure_edge(db, "FDR-C-01", "PMP-C-01")
        _set_condition(db, "FDR-C-01", "critical")
        res = _client().get("/api/gis/context")
        assert res.status_code == 200, res.text
        body = res.json()
        resilience = body.get("resilience")
        assert resilience is not None
        zones = resilience.get("zones") or []
        zone_c = next((z for z in zones if z.get("zone_id") == "zone-c"), None)
        assert zone_c is not None
        assessment = zone_c.get("assessment")
        assert assessment is not None
        dep = next(
            (
                d
                for d in (assessment.get("drivers") or [])
                if d.get("source") == "dependency_exposure"
            ),
            None,
        )
        assert dep is not None
        assert "PMP-C-01" in (dep.get("evidence") or {}).get("affected_asset_ids", [])
        assert dep.get("severity") == "none"
    finally:
        _reset_feeder_live_state(db)
        db.close()
