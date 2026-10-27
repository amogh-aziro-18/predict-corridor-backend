"""Phase 16.0 — Cross-utility dependency foundation (explicit edges only)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.db.seed import seed_all
from app.db.seed.dependencies_catalog import (
    DEPENDENCY_EDGES,
    EXPECTED_DEPENDENCY_COUNT,
)
from app.db.session import SessionLocal
from app.domain.enums import DependencyType
from app.domain.models import AssetDependency
from app.main import app
from app.services.parameter_simulation import project_parameter


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_seed_dependencies_idempotent_and_stable(client: TestClient) -> None:
    first = seed_all()
    second = seed_all()
    assert first["dependencies"] == EXPECTED_DEPENDENCY_COUNT
    assert second["dependencies"] == EXPECTED_DEPENDENCY_COUNT

    db = SessionLocal()
    try:
        rows = db.query(AssetDependency).order_by(AssetDependency.id).all()
        assert len(rows) == EXPECTED_DEPENDENCY_COUNT
        ids = {r.id for r in rows}
        assert ids == {e["id"] for e in DEPENDENCY_EDGES}
        for edge in DEPENDENCY_EDGES:
            row = db.get(AssetDependency, edge["id"])
            assert row is not None
            assert row.source_asset_id == edge["source_asset_id"]
            assert row.target_asset_id == edge["target_asset_id"]
            assert row.dependency_type == DependencyType.SUPPLIES.value
            assert row.enabled is True
            from app.domain.models import Asset

            assert db.get(Asset, row.source_asset_id) is not None
            assert db.get(Asset, row.target_asset_id) is not None
    finally:
        db.close()


def test_self_dependency_rejected() -> None:
    seed_all()
    db = SessionLocal()
    try:
        db.add(
            AssetDependency(
                id="DEP-SELF-BAD",
                source_asset_id="FDR-A-01",
                target_asset_id="FDR-A-01",
                dependency_type=DependencyType.SUPPLIES.value,
                enabled=True,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_invalid_asset_fk_rejected() -> None:
    seed_all()
    db = SessionLocal()
    try:
        db.add(
            AssetDependency(
                id="DEP-MISSING-ASSET",
                source_asset_id="FDR-A-01",
                target_asset_id="DOES-NOT-EXIST",
                dependency_type=DependencyType.SUPPLIES.value,
                enabled=True,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_list_dependencies_api(client: TestClient) -> None:
    seed_all()
    res = client.get("/api/dependencies")
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body) == EXPECTED_DEPENDENCY_COUNT
    edge = next(e for e in body if e["id"] == "DEP-POC-001")
    assert edge["source_asset_id"] == "FDR-A-01"
    assert edge["source_utility"] == "power"
    assert edge["target_asset_id"] == "PMP-A-01"
    assert edge["target_utility"] == "water"
    assert edge["dependency_type"] == "supplies"

    power_only = client.get("/api/dependencies", params={"utility_kind": "power"}).json()
    assert len(power_only) == EXPECTED_DEPENDENCY_COUNT  # all POC edges involve power

    gas_targets = client.get(
        "/api/dependencies", params={"target_asset_id": "CMP-A-01"}
    ).json()
    assert len(gas_targets) == 1
    assert gas_targets[0]["source_asset_id"] == "FDR-A-01"


def test_get_dependencies_for_asset(client: TestClient) -> None:
    seed_all()
    pump = client.get("/api/dependencies/PMP-A-01")
    assert pump.status_code == 200, pump.text
    body = pump.json()
    assert body["available"] is True
    assert {d["asset_id"] for d in body["depends_on"]} == {"FDR-A-01"}
    assert body["depended_by"] == []

    feeder = client.get("/api/dependencies/FDR-A-01").json()
    dep_ids = {d["asset_id"] for d in feeder["depended_by"]}
    assert dep_ids == {"PMP-A-01", "CMP-A-01", "POP-A-01"}
    assert feeder["depends_on"] == []

    lonely = client.get("/api/dependencies/WTP-A-01")
    assert lonely.status_code == 200
    lonely_body = lonely.json()
    assert lonely_body["depends_on"] == []
    assert lonely_body["depended_by"] == []

    missing = client.get("/api/dependencies/DOES-NOT-EXIST")
    assert missing.status_code == 404
    assert "not found" in missing.text.lower()


def test_parameter_what_if_includes_dependencies(client: TestClient) -> None:
    seed_all()
    run = client.post(
        "/api/simulations/parameter-what-if",
        json={
            "asset_id": "PMP-A-01",
            "parameter": "pressure",
            "delta_percent": 5,
        },
    )
    assert run.status_code == 200, run.text
    deps = run.json()["dependencies"]
    assert deps["available"] is True
    assert {d["asset_id"] for d in deps["depends_on"]} == {"FDR-A-01"}
    assert deps["depended_by"] == []

    no_edge = client.post(
        "/api/simulations/parameter-what-if",
        json={
            "asset_id": "WTP-A-01",
            "parameter": "pressure",
            "delta_percent": 5,
        },
    )
    assert no_edge.status_code == 200
    empty = no_edge.json()["dependencies"]
    assert empty["available"] is True
    assert empty["depends_on"] == []
    assert empty["depended_by"] == []
    assert "no explicit" in empty["message"].lower()


def test_projection_math_unchanged() -> None:
    """Gas/Power/Water projection rules must not change in Phase 16.0."""
    gas = project_parameter(
        utility_kind="gas",
        asset_type="gas_compressor",
        parameter="compressor_load_pct",
        baseline_value=92.0,
        delta_percent=10.0,
        absolute_value=None,
    )
    assert gas.available
    assert abs((gas.projected_value or 0) - 101.2) < 0.01
    assert gas.condition == "CRITICAL"

    power = project_parameter(
        utility_kind="power",
        asset_type="transformer",
        parameter="load_pct",
        baseline_value=92.0,
        delta_percent=10.0,
        absolute_value=None,
    )
    assert power.condition == "CRITICAL"
