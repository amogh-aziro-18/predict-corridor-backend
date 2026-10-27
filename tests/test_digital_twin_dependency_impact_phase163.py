"""Phase 16.3 — dependency-aware Digital Twin parameter what-if integration."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db.seed import seed_all
from app.db.session import SessionLocal
from app.domain.models import Asset, Telemetry
from app.domain.models.alert import Alert
from app.domain.models.incident import Incident
from app.domain.models.work_order import WorkOrder
from app.main import app
from app.services.operational_simulation_service import OperationalSimulationService
from app.services.recommendation_service import RecommendationService
from app.api.schemas.telemetry import TelemetryEnvelope
from app.services.telemetry_service import TelemetryService


def _client() -> TestClient:
    return TestClient(app)


def _ensure_load_pct(db, asset_id: str, value: float = 70.0) -> None:
    # Baseline only — skip live Power health evaluation so DT tests stay isolated.
    TelemetryService(db).persist_envelope(
        asset_id=asset_id,
        metric_key="load_pct",
        envelope=TelemetryEnvelope(
            value=value, unit="%", quality="good", metric_key="load_pct"
        ),
        utility_kind="power",
        evaluate_power=False,
        evaluate_gas=False,
    )


def test_critical_projection_with_dependency_fdr_c() -> None:
    seed_all()
    db = SessionLocal()
    try:
        _ensure_load_pct(db, "FDR-C-01")
        body = (
            _client()
            .post(
                "/api/simulations/parameter-what-if",
                json={
                    "asset_id": "FDR-C-01",
                    "parameter": "load_pct",
                    "absolute_value": 99.0,
                },
            )
            .json()
        )
        assert body["projected_condition"] == "CRITICAL"
        impact = body["dependency_impact"]
        assert impact is not None
        assert impact["assessed"] is True
        assert impact["hypothetical"] is True
        assert impact["mutates_operational_state"] is False
        assert impact["source_asset_id"] == "FDR-C-01"
        assert impact["source_utility"] == "power"
        assert impact["source_zone"] == "zone-c"
        assert impact["projected_condition"] == "CRITICAL"
        ids = {a["asset_id"] for a in impact["affected_assets"]}
        assert ids == {"PMP-C-01"}
        assert impact["direct_impact_asset_ids"] == ["PMP-C-01"]
        assert "water" in impact["affected_utilities"]
        assert "zone-c" in impact["affected_zones"]
        assert impact["dependency_paths"]
        text = impact["explanation"].lower()
        assert "potential dependency impact" in text
        assert "pmp-c-01" in text
        assert "will fail" not in text
        assert "zone c will fail" not in text
    finally:
        db.close()


def test_critical_projection_without_dependency_empty() -> None:
    seed_all()
    db = SessionLocal()
    try:
        svc = OperationalSimulationService(db)
        empty = svc._optional_dependency_impact(
            asset_id="WTP-A-01", projected_condition="CRITICAL"
        )
        assert empty is not None
        assert empty["assessed"] is True
        assert empty["affected_asset_count"] == 0
        assert empty["direct_impacts"] == []
        assert empty["propagated_impacts"] == []
        assert empty["affected_assets"] == []
        assert empty["affected_utilities"] == []
        assert empty["direct_impact_asset_ids"] == []
        assert "no potential dependency impact" in empty["explanation"].lower()
    finally:
        db.close()


def test_non_critical_projection_no_dependency_impact_claim() -> None:
    seed_all()
    db = SessionLocal()
    try:
        _ensure_load_pct(db, "FDR-C-01", value=50.0)
        body = (
            _client()
            .post(
                "/api/simulations/parameter-what-if",
                json={
                    "asset_id": "FDR-C-01",
                    "parameter": "load_pct",
                    "absolute_value": 60.0,
                },
            )
            .json()
        )
        assert body["projected_condition"] != "CRITICAL"
        assert body.get("dependency_impact") is None

        svc = OperationalSimulationService(db)
        assert (
            svc._optional_dependency_impact(
                asset_id="FDR-C-01", projected_condition="STABLE"
            )
            is None
        )
        assert (
            svc._optional_dependency_impact(
                asset_id="FDR-C-01", projected_condition="AT RISK"
            )
            is None
        )
    finally:
        db.close()


def test_multi_utility_dependency_fdr_a() -> None:
    seed_all()
    db = SessionLocal()
    try:
        _ensure_load_pct(db, "FDR-A-01")
        body = (
            _client()
            .post(
                "/api/simulations/parameter-what-if",
                json={
                    "asset_id": "FDR-A-01",
                    "parameter": "load_pct",
                    "absolute_value": 99.0,
                },
            )
            .json()
        )
        assert body["projected_condition"] == "CRITICAL"
        impact = body["dependency_impact"]
        assert impact["assessed"] is True
        ids = {a["asset_id"] for a in impact["affected_assets"]}
        assert ids == {"PMP-A-01", "CMP-A-01", "POP-A-01"}
        utils = set(impact["affected_utilities"])
        assert utils == {"gas", "water", "communications"}
        by_id = {a["asset_id"]: a for a in impact["affected_assets"]}
        assert by_id["PMP-A-01"]["utility"] == "water"
        assert by_id["CMP-A-01"]["utility"] == "gas"
        assert by_id["POP-A-01"]["utility"] == "communications"
        assert by_id["PMP-A-01"]["zone"] == "zone-a"
        assert by_id["CMP-A-01"]["zone"] == "zone-a"
        assert by_id["POP-A-01"]["zone"] == "zone-a"
    finally:
        db.close()


def test_simulation_does_not_mutate_operational_state() -> None:
    seed_all()
    db = SessionLocal()
    try:
        _ensure_load_pct(db, "FDR-A-01")
        tel_before = db.query(Telemetry).count()
        alert_before = db.query(Alert).count()
        wo_before = db.query(WorkOrder).count()
        incident_before = db.query(Incident).count()
        health_before = {
            a.id: a.health
            for a in db.query(Asset).filter(
                Asset.id.in_(["FDR-A-01", "PMP-A-01", "CMP-A-01", "POP-A-01"])
            )
        }
        xu_before = [
            r
            for r in RecommendationService(db).list_recommendations()
            if r.source == "cross_utility" and r.affected_asset == "FDR-A-01"
        ]

        body = (
            _client()
            .post(
                "/api/simulations/parameter-what-if",
                json={
                    "asset_id": "FDR-A-01",
                    "parameter": "load_pct",
                    "absolute_value": 99.0,
                },
            )
            .json()
        )
        assert body["dependency_impact"]["assessed"] is True

        db.expire_all()
        # persist_envelope for baseline may add telemetry before the run; count after
        # ensuring baseline should be stable across the what-if itself.
        assert db.query(Alert).count() == alert_before
        assert db.query(WorkOrder).count() == wo_before
        assert db.query(Incident).count() == incident_before
        for asset_id, health in health_before.items():
            assert db.get(Asset, asset_id).health == health

        xu_after = [
            r
            for r in RecommendationService(db).list_recommendations()
            if r.source == "cross_utility" and r.affected_asset == "FDR-A-01"
        ]
        # Live recommendations unchanged by hypothetical DT (no auto-create).
        assert len(xu_after) == len(xu_before)
        # Telemetry may include the ensured baseline row; what-if must not add more.
        assert db.query(Telemetry).count() == tel_before
    finally:
        db.close()


def test_digital_twin_reuses_cross_utility_impact_service() -> None:
    """DT helper must not invent its own traversal — payload mirrors impact service."""
    from app.services.cross_utility_impact_service import CrossUtilityImpactService

    seed_all()
    db = SessionLocal()
    try:
        assessment = CrossUtilityImpactService(db).assess_asset_impact(
            asset_id="FDR-C-01", condition="critical"
        )
        dt = OperationalSimulationService(db)._optional_dependency_impact(
            asset_id="FDR-C-01", projected_condition="CRITICAL"
        )
        assert dt is not None
        assert {a["asset_id"] for a in dt["affected_assets"]} == {
            a["asset_id"] for a in assessment["affected_assets"]
        }
        assert set(dt["affected_utilities"]) == set(assessment["affected_utilities"])
        assert set(dt["affected_zones"]) == set(assessment["affected_zones"])
        assert len(dt["dependency_paths"]) == len(assessment["dependency_paths"])
        assert dt["hypothetical"] is True
    finally:
        db.close()
