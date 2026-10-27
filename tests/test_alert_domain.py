"""Alert domain API, fingerprint, and Water producer tests (Phase 12)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.domain.enums import AlertScope, AlertSource, AlertStatus
from app.domain.models import Asset
from app.domain.repositories import AlertRepository, AssetRepository
from app.main import app
from app.services.alert_service import (
    AlertService,
    build_alert_fingerprint,
    scope_key_for,
)
from app.utilities.water.alert_producer import (
    known_water_alert_codes,
    sync_water_asset_alerts,
    water_alert_meta,
)
from app.utilities.water import health as water_health


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _delete_alerts_for_asset(asset_id: str) -> None:
    db = SessionLocal()
    try:
        rows = AlertRepository(db).list(asset_id=asset_id)
        for row in rows:
            AlertRepository(db).delete(row)
    finally:
        db.close()


def _pick_water_asset() -> Asset:
    db = SessionLocal()
    try:
        assets = AssetRepository(db).list(utility_kind="water")
        if not assets:
            pytest.skip("No water assets")
        # Detach fields we need
        a = assets[0]
        db.expunge(a)
        return a
    finally:
        db.close()


def test_openapi_includes_alert_routes() -> None:
    paths = app.openapi()["paths"]
    assert "/api/alerts" in paths
    assert "/api/alerts/{alert_id}" in paths
    assert "/api/alerts/{alert_id}/status" in paths


def test_fingerprint_stability_and_scope_utility_separation() -> None:
    a = build_alert_fingerprint(
        source="telemetry",
        alert_code="pressure_low",
        scope_key="asset:A1",
        utility_kind="water",
    )
    b = build_alert_fingerprint(
        source="telemetry",
        alert_code="pressure_low",
        scope_key="asset:A1",
        utility_kind="water",
    )
    assert a == b
    other_util = build_alert_fingerprint(
        source="telemetry",
        alert_code="pressure_low",
        scope_key="asset:A1",
        utility_kind="power",
    )
    assert a != other_util
    other_scope = build_alert_fingerprint(
        source="telemetry",
        alert_code="pressure_low",
        scope_key="asset:A2",
        utility_kind="water",
    )
    assert a != other_scope
    assert scope_key_for(scope="corridor", zone_id=None, asset_id=None) == "corridor"


def test_upsert_dedup_and_severity_update() -> None:
    asset = _pick_water_asset()
    _delete_alerts_for_asset(asset.id)
    db = SessionLocal()
    try:
        svc = AlertService(db)
        first = svc.upsert_condition(
            source=AlertSource.TELEMETRY.value,
            alert_code="pressure_low",
            title="Low pressure",
            description="t",
            severity="high",
            utility_kind="water",
            scope=AlertScope.ASSET.value,
            zone_id=asset.zone_id,
            asset_id=asset.id,
        )
        second = svc.upsert_condition(
            source=AlertSource.TELEMETRY.value,
            alert_code="pressure_low",
            title="Low pressure",
            description="t",
            severity="critical",
            utility_kind="water",
            scope=AlertScope.ASSET.value,
            zone_id=asset.zone_id,
            asset_id=asset.id,
        )
        assert first.id == second.id
        assert second.severity == "critical"
        active = AlertRepository(db).list(asset_id=asset.id, active_only=True)
        assert len([a for a in active if a.alert_code == "pressure_low"]) == 1
    finally:
        db.close()
        _delete_alerts_for_asset(asset.id)


def test_resolved_permits_future_occurrence() -> None:
    asset = _pick_water_asset()
    _delete_alerts_for_asset(asset.id)
    db = SessionLocal()
    try:
        svc = AlertService(db)
        first = svc.upsert_condition(
            source=AlertSource.TELEMETRY.value,
            alert_code="flow_low",
            title="Low flow",
            description=None,
            severity="high",
            utility_kind="water",
            scope=AlertScope.ASSET.value,
            zone_id=asset.zone_id,
            asset_id=asset.id,
        )
        svc.update_status(
            first.id,
            __import__("app.api.schemas.alert", fromlist=["AlertStatusUpdate"]).AlertStatusUpdate(
                status="resolved", notes="cleared"
            ),
        )
        second = svc.upsert_condition(
            source=AlertSource.TELEMETRY.value,
            alert_code="flow_low",
            title="Low flow",
            description=None,
            severity="high",
            utility_kind="water",
            scope=AlertScope.ASSET.value,
            zone_id=asset.zone_id,
            asset_id=asset.id,
        )
        assert second.id != first.id
        assert second.status == AlertStatus.OPEN.value
        assert second.fingerprint == first.fingerprint
    finally:
        db.close()
        _delete_alerts_for_asset(asset.id)


def test_api_list_get_ack_resolve_and_filters(client: TestClient) -> None:
    asset = _pick_water_asset()
    _delete_alerts_for_asset(asset.id)
    db = SessionLocal()
    try:
        alert = AlertService(db).upsert_condition(
            source=AlertSource.TELEMETRY.value,
            alert_code="pump_stopped",
            title="Pump stopped",
            description="test",
            severity="high",
            utility_kind="water",
            scope=AlertScope.ASSET.value,
            zone_id=asset.zone_id,
            asset_id=asset.id,
        )
        alert_id = alert.id
    finally:
        db.close()

    try:
        listing = client.get(
            "/api/alerts",
            params={"status": "open", "severity": "high", "utility_kind": "water", "source": "telemetry"},
        )
        assert listing.status_code == 200
        ids = {row["id"] for row in listing.json()}
        assert alert_id in ids

        detail = client.get(f"/api/alerts/{alert_id}")
        assert detail.status_code == 200
        assert detail.json()["alert_code"] == "pump_stopped"

        ack = client.patch(
            f"/api/alerts/{alert_id}/status",
            json={"status": "acknowledged", "notes": "seen"},
        )
        assert ack.status_code == 200
        assert ack.json()["status"] == "acknowledged"
        assert ack.json()["acknowledged_at"] is not None
        assert ack.json()["ack_notes"] == "seen"

        bad = client.patch(
            f"/api/alerts/{alert_id}/status",
            json={"status": "open"},
        )
        assert bad.status_code == 422

        resolved = client.patch(
            f"/api/alerts/{alert_id}/status",
            json={"status": "resolved", "notes": "done"},
        )
        assert resolved.status_code == 200
        assert resolved.json()["status"] == "resolved"
        assert resolved.json()["resolved_at"] is not None

        terminal = client.patch(
            f"/api/alerts/{alert_id}/status",
            json={"status": "acknowledged"},
        )
        assert terminal.status_code == 422
    finally:
        _delete_alerts_for_asset(asset.id)


def test_water_codes_map_and_evaluate_alerts_roundtrip() -> None:
    codes = water_health.evaluate_alerts({"pressure": 1.0})
    assert "pressure_critical_low" in codes
    assert "pressure_critical_low" in known_water_alert_codes()
    title, _desc, sev = water_alert_meta("pressure_critical_low")
    assert sev == "critical"
    assert "pressure" in title.lower() or "Pressure" in title

    # No fake alerts when metrics are healthy
    assert water_health.evaluate_alerts({"pressure": 3.0, "flow_rate": 200.0}) == []


def test_water_producer_set_diff_no_duplicates() -> None:
    asset = _pick_water_asset()
    _delete_alerts_for_asset(asset.id)
    db = SessionLocal()
    try:
        # Re-load attached asset
        row = AssetRepository(db).get(asset.id)
        assert row is not None
        sync_water_asset_alerts(db, asset=row, codes=["pressure_low", "flow_low"])
        db.commit()
        sync_water_asset_alerts(db, asset=row, codes=["pressure_low", "flow_low"])
        db.commit()
        active = AlertRepository(db).list_active_for_asset(
            asset_id=asset.id, source="telemetry", utility_kind="water"
        )
        assert len(active) == 2

        # Clear flow_low only
        sync_water_asset_alerts(db, asset=row, codes=["pressure_low"])
        db.commit()
        active2 = AlertRepository(db).list_active_for_asset(
            asset_id=asset.id, source="telemetry", utility_kind="water"
        )
        assert len(active2) == 1
        assert active2[0].alert_code == "pressure_low"

        # Unrelated alert remains if we only clear known codes for this asset
        all_for_asset = AlertRepository(db).list(asset_id=asset.id)
        resolved_flow = [a for a in all_for_asset if a.alert_code == "flow_low"]
        assert resolved_flow
        assert resolved_flow[0].status == "resolved"
    finally:
        db.close()
        _delete_alerts_for_asset(asset.id)


def test_water_sync_profile_metric_persists_alert(client: TestClient) -> None:
    """Integration: WaterService.sync_profile_metric → Alert upsert."""
    asset = _pick_water_asset()
    _delete_alerts_for_asset(asset.id)
    db = SessionLocal()
    try:
        from app.services.water_service import WaterService

        # Force critical pressure via profile path
        row = AssetRepository(db).get(asset.id)
        assert row is not None
        profile = dict(row.profile or {})
        profile["pressure"] = 1.0  # <= PRESSURE_CRITICAL_LOW
        row.profile = profile
        db.commit()

        WaterService(db).sync_profile_metric(asset.id, "pressure", 1.0)
        db.commit()

        active = AlertRepository(db).list_active_for_asset(
            asset_id=asset.id, source="telemetry", utility_kind="water"
        )
        codes = {a.alert_code for a in active}
        assert "pressure_critical_low" in codes

        # Clear pressure to healthy
        WaterService(db).sync_profile_metric(asset.id, "pressure", 3.0)
        db.commit()
        active2 = AlertRepository(db).list_active_for_asset(
            asset_id=asset.id, source="telemetry", utility_kind="water"
        )
        assert "pressure_critical_low" not in {a.alert_code for a in active2}
    finally:
        db.close()
        _delete_alerts_for_asset(asset.id)


def test_alert_not_found(client: TestClient) -> None:
    assert client.get("/api/alerts/ALR-DOES-NOT-EXIST").status_code == 404


def test_known_water_codes_match_evaluate_alerts_vocabulary() -> None:
    """Producer only maps real evaluate_alerts codes."""
    known = known_water_alert_codes()
    expected = {
        "pressure_critical_low",
        "pressure_low",
        "pressure_high",
        "level_critical",
        "level_low",
        "flow_low",
        "pump_stopped",
        "turbidity_high",
    }
    assert known == expected
