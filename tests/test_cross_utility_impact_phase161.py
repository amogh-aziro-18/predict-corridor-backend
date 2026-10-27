"""Phase 16.1 — deterministic cross-utility dependency impact."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db.seed import seed_all
from app.db.session import SessionLocal
from app.domain.enums import DependencyType
from app.domain.models import Asset, AssetDependency, Telemetry
from app.domain.models.alert import Alert
from app.domain.models.work_order import WorkOrder
from app.main import app
from app.services.cross_utility_impact_service import CrossUtilityImpactService


def _client() -> TestClient:
    return TestClient(app)


def test_direct_impact_fdr_c_to_pmp_c() -> None:
    seed_all()
    client = _client()
    res = client.post(
        "/api/impact/asset",
        json={"asset_id": "FDR-C-01", "condition": "unavailable"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source_asset_id"] == "FDR-C-01"
    assert body["source_utility"] == "power"
    assert body["source_zone"] == "zone-c"
    assert body["trigger_condition"] == "unavailable"
    assert body["mutates_operational_state"] is False
    ids = {a["asset_id"] for a in body["direct_impacts"]}
    assert ids == {"PMP-C-01"}
    assert body["propagated_impacts"] == []
    assert "water" in body["affected_utilities"]
    assert "zone-c" in body["affected_zones"]
    assert all(a["posture"] == "potentially_affected" for a in body["affected_assets"])


def test_cross_utility_impact_fdr_a() -> None:
    seed_all()
    client = _client()
    body = client.post(
        "/api/impact/asset",
        json={"asset_id": "FDR-A-01", "condition": "unavailable"},
    ).json()
    ids = {a["asset_id"] for a in body["affected_assets"]}
    assert ids == {"PMP-A-01", "CMP-A-01", "POP-A-01"}
    assert set(body["affected_utilities"]) == {"gas", "water", "communications"}
    assert body["affected_zones"] == ["zone-a"]
    assert body["affected_asset_count"] == 3
    assert body["affected_utility_count"] == 3
    assert body["affected_zone_count"] == 1
    assert body.get("mutates_operational_state") is False


def test_no_outgoing_dependencies() -> None:
    seed_all()
    client = _client()
    body = client.post(
        "/api/impact/asset",
        json={"asset_id": "WTP-A-01", "condition": "unavailable"},
    ).json()
    assert body["direct_impacts"] == []
    assert body["propagated_impacts"] == []
    assert body["affected_utilities"] == []
    assert body["affected_zones"] == []
    assert body["affected_asset_count"] == 0


def test_directionality_pump_does_not_affect_feeder() -> None:
    seed_all()
    client = _client()
    body = client.post(
        "/api/impact/asset",
        json={"asset_id": "PMP-A-01", "condition": "unavailable"},
    ).json()
    ids = {a["asset_id"] for a in body["affected_assets"]}
    assert "FDR-A-01" not in ids
    assert body["affected_asset_count"] == 0


def test_transitive_traversal_test_only_chain() -> None:
    seed_all()
    db = SessionLocal()
    try:
        # Temporary chain using stable assets: FDR-D-01 → XFMR-D-01 → SUB-D-01
        db.merge(
            AssetDependency(
                id="DEP-TEST-T1",
                source_asset_id="FDR-D-01",
                target_asset_id="XFMR-D-01",
                dependency_type=DependencyType.SUPPLIES.value,
                enabled=True,
            )
        )
        db.merge(
            AssetDependency(
                id="DEP-TEST-T2",
                source_asset_id="XFMR-D-01",
                target_asset_id="SUB-D-01",
                dependency_type=DependencyType.SUPPLIES.value,
                enabled=True,
            )
        )
        db.commit()

        svc = CrossUtilityImpactService(db)
        result = svc.assess_asset_impact(
            asset_id="FDR-D-01", condition="unavailable", max_depth=5
        )
        ids = {a["asset_id"] for a in result["affected_assets"]}
        assert ids == {"XFMR-D-01", "SUB-D-01"}
        kinds = {a["asset_id"]: a["impact_kind"] for a in result["affected_assets"]}
        assert kinds["XFMR-D-01"] == "direct"
        assert kinds["SUB-D-01"] == "propagated"
        depth1 = next(p for p in result["dependency_paths"] if p["asset_id"] == "SUB-D-01")
        assert depth1["path_asset_ids"] == ["FDR-D-01", "XFMR-D-01", "SUB-D-01"]

        shallow = svc.assess_asset_impact(
            asset_id="FDR-D-01", condition="unavailable", max_depth=1
        )
        assert {a["asset_id"] for a in shallow["affected_assets"]} == {"XFMR-D-01"}
    finally:
        db.query(AssetDependency).filter(
            AssetDependency.id.in_(["DEP-TEST-T1", "DEP-TEST-T2"])
        ).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_cycle_protection() -> None:
    seed_all()
    db = SessionLocal()
    try:
        db.merge(
            AssetDependency(
                id="DEP-TEST-C1",
                source_asset_id="SUB-B-01",
                target_asset_id="XFMR-B-01",
                dependency_type=DependencyType.SUPPLIES.value,
                enabled=True,
            )
        )
        db.merge(
            AssetDependency(
                id="DEP-TEST-C2",
                source_asset_id="XFMR-B-01",
                target_asset_id="SUB-B-01",
                dependency_type=DependencyType.SUPPLIES.value,
                enabled=True,
            )
        )
        db.commit()
        result = CrossUtilityImpactService(db).assess_asset_impact(
            asset_id="SUB-B-01", condition="unavailable", max_depth=10
        )
        # Visits XFMR once; cycle back to SUB is skipped (already seen).
        assert {a["asset_id"] for a in result["affected_assets"]} == {"XFMR-B-01"}
        assert result["affected_asset_count"] == 1
    finally:
        db.query(AssetDependency).filter(
            AssetDependency.id.in_(["DEP-TEST-C1", "DEP-TEST-C2"])
        ).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_unknown_asset_and_invalid_condition() -> None:
    seed_all()
    client = _client()
    missing = client.post(
        "/api/impact/asset",
        json={"asset_id": "DOES-NOT-EXIST", "condition": "unavailable"},
    )
    assert missing.status_code == 404

    bad = client.post(
        "/api/impact/asset",
        json={"asset_id": "FDR-A-01", "condition": "totally_made_up"},
    )
    assert bad.status_code == 422


def test_impact_does_not_mutate_operational_state() -> None:
    seed_all()
    db = SessionLocal()
    try:
        tel_before = db.query(Telemetry).count()
        alert_before = db.query(Alert).count()
        wo_before = db.query(WorkOrder).count()
        watched = ["FDR-A-01", "PMP-A-01", "CMP-A-01", "POP-A-01"]
        health_before = {
            a.id: a.health
            for a in db.query(Asset).filter(Asset.id.in_(watched))
        }

        CrossUtilityImpactService(db).assess_asset_impact(
            asset_id="FDR-A-01", condition="unavailable"
        )
        db.expire_all()

        assert db.query(Telemetry).count() == tel_before
        assert db.query(Alert).count() == alert_before
        assert db.query(WorkOrder).count() == wo_before
        for asset_id, health in health_before.items():
            assert db.get(Asset, asset_id).health == health
    finally:
        db.close()


def test_optional_dependency_impact_only_when_critical_with_dependents() -> None:
    """Digital Twin helper attaches impact for CRITICAL + outgoing supplies only."""
    from app.services.operational_simulation_service import OperationalSimulationService

    seed_all()
    db = SessionLocal()
    try:
        svc = OperationalSimulationService(db)
        hit = svc._optional_dependency_impact(
            asset_id="FDR-A-01", projected_condition="CRITICAL"
        )
        assert hit is not None
        assert hit["assessed"] is True
        assert set(hit["direct_impact_asset_ids"]) == {"PMP-A-01", "CMP-A-01", "POP-A-01"}
        assert set(hit["affected_utilities"]) == {"gas", "water", "communications"}
        assert hit.get("mutates_operational_state") is False

        assert (
            svc._optional_dependency_impact(
                asset_id="FDR-A-01", projected_condition="STABLE"
            )
            is None
        )
        # Phase 16.3: CRITICAL with no dependents → honest empty assessed result.
        empty = svc._optional_dependency_impact(
            asset_id="WTP-A-01", projected_condition="CRITICAL"
        )
        assert empty is not None
        assert empty["assessed"] is True
        assert empty["affected_asset_count"] == 0
        assert empty["direct_impacts"] == []
        assert empty["direct_impact_asset_ids"] == []
        assert "no potential dependency impact" in empty["explanation"].lower()
    finally:
        db.close()
