"""Phase 17.5 — Communications alerts (set-diff) + operational recommendations."""

from __future__ import annotations

from app.db.seed import seed_all
from app.db.session import SessionLocal
from app.domain.enums import AlertStatus
from app.domain.models import Alert, Asset, WorkOrder
from app.domain.repositories import AlertRepository
from app.api.schemas.telemetry import TelemetryEnvelope
from app.services.alert_service import AlertService, build_alert_fingerprint, scope_key_for
from app.services.communications_service import CommunicationsService
from app.services.operational_intelligence_service import OperationalIntelligenceService
from app.services.recommendation_service import (
    RecommendationService,
    _infer_recommendation_source,
)
from app.services.telemetry_service import TelemetryService
from app.utilities.communications.alert_producer import (
    known_communications_alert_codes,
    sync_communications_asset_alerts,
)
from app.utilities.communications import health as comm_health

_UNITS = {
    "link_rtt_ms": "ms",
    "packet_loss_pct": "%",
    "link_up": "0-1",
    "cabinet_temp_c": "°C",
    "power_draw_w": "W",
}


def _seed() -> None:
    seed_all()


def _clear_comm_alerts(db, asset_id: str | None = None) -> None:
    q = db.query(Alert).filter(Alert.utility_kind == "communications")
    if asset_id:
        q = q.filter(Alert.asset_id == asset_id)
    for row in q.all():
        AlertRepository(db).delete(row)
    db.commit()


def _set_profile_and_evaluate(
    db,
    *,
    asset_id: str,
    metrics: dict[str, float],
    nominals: dict[str, float] | None = None,
) -> Asset:
    asset = db.query(Asset).filter(Asset.id == asset_id).one()
    profile = dict(asset.profile or {})
    defaults = {
        "nominal_link_rtt_ms": 12.0 if asset.type == "edge_pop" else 18.0,
        "nominal_packet_loss_pct": 0.2 if asset.type == "edge_pop" else 0.4,
        "nominal_cabinet_temp_c": 28.0 if asset.type == "edge_pop" else 30.0,
        "nominal_power_draw_w": 180.0 if asset.type == "edge_pop" else 45.0,
    }
    if nominals:
        defaults.update(nominals)
    profile.update(defaults)
    profile.update(metrics)
    asset.profile = profile
    db.flush()

    # Persist telemetry so HISTORY_LIMIT latest samples match intended metrics.
    svc = TelemetryService(db)
    for metric_key, value in metrics.items():
        for _ in range(3):
            svc.persist_envelope(
                asset_id=asset_id,
                metric_key=metric_key,
                envelope=TelemetryEnvelope(
                    value=float(value),
                    unit=_UNITS.get(metric_key, ""),
                    quality="good",
                    metric_key=metric_key,
                ),
                utility_kind="communications",
                commit=False,
                evaluate_communications=False,
            )
    CommunicationsService(db).evaluate_condition(asset_id)
    db.commit()
    db.refresh(asset)
    return asset


def test_qualifying_finding_creates_alert() -> None:
    _seed()
    db = SessionLocal()
    try:
        asset = db.query(Asset).filter(Asset.id == "POP-A-01").one()
        _clear_comm_alerts(db, asset.id)
        sync_communications_asset_alerts(
            db, asset=asset, codes=["rtt_elevated", "packet_loss_elevated"]
        )
        db.commit()
        open_alerts = (
            db.query(Alert)
            .filter(
                Alert.asset_id == asset.id,
                Alert.utility_kind == "communications",
                Alert.status == AlertStatus.OPEN.value,
            )
            .all()
        )
        assert {a.alert_code for a in open_alerts} == {
            "rtt_elevated",
            "packet_loss_elevated",
        }
        assert all(a.utility_kind == "communications" for a in open_alerts)
        assert all(a.source == "telemetry" for a in open_alerts)
    finally:
        db.close()


def test_severity_mapping() -> None:
    _seed()
    db = SessionLocal()
    try:
        asset = db.query(Asset).filter(Asset.id == "POP-B-01").one()
        _clear_comm_alerts(db, asset.id)
        sync_communications_asset_alerts(
            db,
            asset=asset,
            codes=[
                "rtt_elevated",
                "rtt_rising",
                "rtt_high",
                "path_quality_stress",
                "rtt_critical",
                "link_down",
            ],
        )
        db.commit()
        by_code = {
            a.alert_code: a.severity
            for a in db.query(Alert)
            .filter(
                Alert.asset_id == asset.id,
                Alert.status == AlertStatus.OPEN.value,
            )
            .all()
        }
        assert by_code["rtt_elevated"] == "medium"
        assert by_code["rtt_rising"] == "medium"
        assert by_code["rtt_high"] == "high"
        assert by_code["path_quality_stress"] == "high"
        assert by_code["rtt_critical"] == "critical"
        assert by_code["link_down"] == "critical"
    finally:
        db.close()


def test_fingerprint_matches_build_alert_fingerprint() -> None:
    _seed()
    db = SessionLocal()
    try:
        asset = db.query(Asset).filter(Asset.id == "GW-A-01").one()
        _clear_comm_alerts(db, asset.id)
        sync_communications_asset_alerts(db, asset=asset, codes=["packet_loss_high"])
        db.commit()
        row = (
            db.query(Alert)
            .filter(
                Alert.asset_id == asset.id,
                Alert.alert_code == "packet_loss_high",
                Alert.status == AlertStatus.OPEN.value,
            )
            .one()
        )
        expected = build_alert_fingerprint(
            source="telemetry",
            alert_code="packet_loss_high",
            scope_key=scope_key_for(scope="asset", zone_id=asset.zone_id, asset_id=asset.id),
            utility_kind="communications",
        )
        assert row.fingerprint == expected
    finally:
        db.close()


def test_repeat_evaluation_does_not_duplicate() -> None:
    _seed()
    db = SessionLocal()
    try:
        asset = db.query(Asset).filter(Asset.id == "POP-C-01").one()
        _clear_comm_alerts(db, asset.id)
        codes = ["cabinet_temp_high", "power_draw_elevated"]
        sync_communications_asset_alerts(db, asset=asset, codes=codes)
        db.commit()
        first = (
            db.query(Alert)
            .filter(
                Alert.asset_id == asset.id,
                Alert.utility_kind == "communications",
                Alert.status == AlertStatus.OPEN.value,
            )
            .count()
        )
        sync_communications_asset_alerts(db, asset=asset, codes=codes)
        db.commit()
        second = (
            db.query(Alert)
            .filter(
                Alert.asset_id == asset.id,
                Alert.utility_kind == "communications",
                Alert.status == AlertStatus.OPEN.value,
            )
            .count()
        )
        assert first == len(codes)
        assert second == first
    finally:
        db.close()


def test_pop_and_gateway_same_code_distinct() -> None:
    _seed()
    db = SessionLocal()
    try:
        pop = db.query(Asset).filter(Asset.id == "POP-A-01").one()
        gw = db.query(Asset).filter(Asset.id == "GW-A-01").one()
        _clear_comm_alerts(db)  # wipe all Comm alerts so leftover codes don't leak
        sync_communications_asset_alerts(db, asset=pop, codes=["rtt_high"])
        sync_communications_asset_alerts(db, asset=gw, codes=["rtt_high"])
        db.commit()
        rows = (
            db.query(Alert)
            .filter(
                Alert.alert_code == "rtt_high",
                Alert.utility_kind == "communications",
                Alert.status == AlertStatus.OPEN.value,
                Alert.asset_id.in_(("POP-A-01", "GW-A-01")),
            )
            .all()
        )
        assert {r.asset_id for r in rows} == {"POP-A-01", "GW-A-01"}
        assert len({r.fingerprint for r in rows}) == 2
    finally:
        db.close()


def test_different_codes_remain_distinct() -> None:
    _seed()
    db = SessionLocal()
    try:
        asset = db.query(Asset).filter(Asset.id == "POP-A-01").one()
        _clear_comm_alerts(db, asset.id)
        sync_communications_asset_alerts(
            db, asset=asset, codes=["rtt_high", "packet_loss_high"]
        )
        db.commit()
        rows = (
            db.query(Alert)
            .filter(
                Alert.asset_id == asset.id,
                Alert.status == AlertStatus.OPEN.value,
            )
            .all()
        )
        assert len(rows) == 2
        assert len({r.fingerprint for r in rows}) == 2
    finally:
        db.close()


def test_cleared_code_resolves_previous_alert() -> None:
    _seed()
    db = SessionLocal()
    try:
        asset = db.query(Asset).filter(Asset.id == "POP-A-01").one()
        _clear_comm_alerts(db, asset.id)
        sync_communications_asset_alerts(db, asset=asset, codes=["rtt_high"])
        db.commit()
        assert (
            db.query(Alert)
            .filter(
                Alert.asset_id == asset.id,
                Alert.alert_code == "rtt_high",
                Alert.status == AlertStatus.OPEN.value,
            )
            .count()
            == 1
        )
        sync_communications_asset_alerts(db, asset=asset, codes=[])
        db.commit()
        resolved = (
            db.query(Alert)
            .filter(
                Alert.asset_id == asset.id,
                Alert.alert_code == "rtt_high",
                Alert.status == AlertStatus.RESOLVED.value,
            )
            .one()
        )
        assert "cleared" in (resolved.resolve_notes or "").lower()
    finally:
        db.close()


def test_severity_change_updates_same_fingerprint() -> None:
    _seed()
    db = SessionLocal()
    try:
        asset = db.query(Asset).filter(Asset.id == "GW-B-01").one()
        _clear_comm_alerts(db, asset.id)
        sync_communications_asset_alerts(db, asset=asset, codes=["rtt_elevated"])
        db.commit()
        first = (
            db.query(Alert)
            .filter(
                Alert.asset_id == asset.id,
                Alert.alert_code == "rtt_elevated",
                Alert.status == AlertStatus.OPEN.value,
            )
            .one()
        )
        fp = first.fingerprint
        # Escalate via sync of a different severity code on same finding family —
        # use rtt_critical on same asset; elevated resolves, critical opens.
        # For same-code severity update: re-upsert elevated then manually verify
        # update path by syncing rtt_high after elevated (different codes).
        # Same fingerprint severity update: call upsert via sync with same code
        # after meta would change — producer meta is fixed per code, so simulate
        # update by AlertService.upsert_condition with new severity.
        AlertService(db).upsert_condition(
            source="telemetry",
            alert_code="rtt_elevated",
            title=first.title,
            description=first.description,
            severity="high",
            utility_kind="communications",
            scope="asset",
            zone_id=asset.zone_id,
            asset_id=asset.id,
        )
        db.commit()
        updated = (
            db.query(Alert)
            .filter(
                Alert.asset_id == asset.id,
                Alert.alert_code == "rtt_elevated",
                Alert.status == AlertStatus.OPEN.value,
            )
            .one()
        )
        assert updated.fingerprint == fp
        assert updated.severity == "high"
        assert (
            db.query(Alert)
            .filter(
                Alert.asset_id == asset.id,
                Alert.alert_code == "rtt_elevated",
                Alert.status == AlertStatus.OPEN.value,
            )
            .count()
            == 1
        )
    finally:
        db.close()


def test_healthy_evaluation_no_open_alert() -> None:
    _seed()
    db = SessionLocal()
    try:
        _clear_comm_alerts(db, "POP-A-01")
        asset = _set_profile_and_evaluate(
            db,
            asset_id="POP-A-01",
            metrics={
                "link_rtt_ms": 12.0,
                "packet_loss_pct": 0.2,
                "link_up": 1.0,
                "cabinet_temp_c": 28.0,
                "power_draw_w": 180.0,
            },
        )
        assert asset.profile.get("condition") == "healthy"
        open_alerts = (
            db.query(Alert)
            .filter(
                Alert.asset_id == "POP-A-01",
                Alert.utility_kind == "communications",
                Alert.status == AlertStatus.OPEN.value,
            )
            .count()
        )
        assert open_alerts == 0
    finally:
        db.close()


def test_link_down_produces_critical_alert() -> None:
    _seed()
    db = SessionLocal()
    try:
        _clear_comm_alerts(db, "POP-A-01")
        asset = _set_profile_and_evaluate(
            db,
            asset_id="POP-A-01",
            metrics={
                "link_rtt_ms": 12.0,
                "packet_loss_pct": 35.0,
                "link_up": 0.0,
                "cabinet_temp_c": 28.0,
                "power_draw_w": 20.0,
            },
        )
        assert asset.profile.get("condition") == "unavailable"
        row = (
            db.query(Alert)
            .filter(
                Alert.asset_id == "POP-A-01",
                Alert.alert_code == "link_down",
                Alert.status == AlertStatus.OPEN.value,
            )
            .one()
        )
        assert row.severity == "critical"
        assert row.source == "telemetry"
        # Must not invent alert_code "unavailable"
        assert (
            db.query(Alert)
            .filter(
                Alert.asset_id == "POP-A-01",
                Alert.alert_code == "unavailable",
            )
            .count()
            == 0
        )
    finally:
        db.close()


def test_evaluate_condition_creates_alerts_now() -> None:
    _seed()
    db = SessionLocal()
    try:
        _clear_comm_alerts(db, "GW-C-01")
        _set_profile_and_evaluate(
            db,
            asset_id="GW-C-01",
            metrics={
                "link_rtt_ms": 18.0 + 40.0,
                "packet_loss_pct": 0.4,
                "link_up": 1.0,
                "cabinet_temp_c": 30.0,
                "power_draw_w": 45.0,
            },
        )
        open_alerts = (
            db.query(Alert)
            .filter(
                Alert.asset_id == "GW-C-01",
                Alert.utility_kind == "communications",
                Alert.status == AlertStatus.OPEN.value,
            )
            .all()
        )
        assert open_alerts
        assert any(a.alert_code == "rtt_critical" for a in open_alerts)
        assert all(a.alert_code in known_communications_alert_codes() for a in open_alerts)
    finally:
        db.close()


def _oi_for_asset(db, asset_id: str) -> list:
    return [
        d
        for d in OperationalIntelligenceService(db).generate(
            scenario_key="normal", scenario_intensity=0.0
        )
        if d.affected_asset == asset_id and d.rule_code.startswith("communications_")
    ]


def test_oi_link_outage() -> None:
    _seed()
    db = SessionLocal()
    try:
        _set_profile_and_evaluate(
            db,
            asset_id="POP-D-01",
            metrics={
                "link_rtt_ms": 12.0,
                "packet_loss_pct": 35.0,
                "link_up": 0.0,
                "cabinet_temp_c": 32.0,
                "power_draw_w": 25.0,
            },
        )
        drafts = _oi_for_asset(db, "POP-D-01")
        assert any(d.rule_code == "communications_link_outage" for d in drafts)
        chosen = next(d for d in drafts if d.rule_code == "communications_link_outage")
        assert chosen.priority == "critical"
        assert chosen.confidence == 0.90
        assert chosen.affected_zone == "zone-d"
        assert "restore the link" in chosen.recommendation.lower()
    finally:
        db.close()


def test_oi_critical_path() -> None:
    _seed()
    db = SessionLocal()
    try:
        _set_profile_and_evaluate(
            db,
            asset_id="POP-A-01",
            metrics={
                "link_rtt_ms": 12.0 + 40.0,
                "packet_loss_pct": 0.2,
                "link_up": 1.0,
                "cabinet_temp_c": 28.0,
                "power_draw_w": 180.0,
            },
        )
        drafts = _oi_for_asset(db, "POP-A-01")
        assert any(d.rule_code == "communications_critical_path" for d in drafts)
    finally:
        db.close()


def test_oi_path_quality_stress() -> None:
    _seed()
    db = SessionLocal()
    try:
        # rtt_high + packet_loss_elevated → path_quality_stress (no critical)
        _set_profile_and_evaluate(
            db,
            asset_id="POP-B-01",
            metrics={
                "link_rtt_ms": 12.0 + 20.0,
                "packet_loss_pct": 1.5,
                "link_up": 1.0,
                "cabinet_temp_c": 28.0,
                "power_draw_w": 180.0,
            },
        )
        asset = db.query(Asset).filter(Asset.id == "POP-B-01").one()
        assert "path_quality_stress" in (asset.profile or {}).get(
            "condition_alert_codes", []
        )
        drafts = _oi_for_asset(db, "POP-B-01")
        assert any(d.rule_code == "communications_path_quality_stress" for d in drafts)
    finally:
        db.close()


def test_oi_cabinet_thermal() -> None:
    _seed()
    db = SessionLocal()
    try:
        _set_profile_and_evaluate(
            db,
            asset_id="GW-A-01",
            metrics={
                "link_rtt_ms": 18.0,
                "packet_loss_pct": 0.4,
                "link_up": 1.0,
                "cabinet_temp_c": 55.0,
                "power_draw_w": 45.0,
            },
        )
        drafts = _oi_for_asset(db, "GW-A-01")
        assert any(d.rule_code == "communications_cabinet_thermal" for d in drafts)
        chosen = next(d for d in drafts if d.rule_code == "communications_cabinet_thermal")
        assert chosen.priority == "high"
    finally:
        db.close()


def test_oi_rtt_degraded() -> None:
    _seed()
    db = SessionLocal()
    try:
        _set_profile_and_evaluate(
            db,
            asset_id="POP-A-01",
            metrics={
                "link_rtt_ms": 12.0 + 20.0,
                "packet_loss_pct": 0.2,
                "link_up": 1.0,
                "cabinet_temp_c": 28.0,
                "power_draw_w": 180.0,
            },
        )
        drafts = _oi_for_asset(db, "POP-A-01")
        assert any(d.rule_code == "communications_rtt_degraded" for d in drafts)
    finally:
        db.close()


def test_oi_packet_loss() -> None:
    _seed()
    db = SessionLocal()
    try:
        _set_profile_and_evaluate(
            db,
            asset_id="GW-B-01",
            metrics={
                "link_rtt_ms": 18.0,
                "packet_loss_pct": 6.0,
                "link_up": 1.0,
                "cabinet_temp_c": 30.0,
                "power_draw_w": 45.0,
            },
        )
        drafts = _oi_for_asset(db, "GW-B-01")
        assert any(d.rule_code == "communications_packet_loss" for d in drafts)
    finally:
        db.close()


def test_oi_power_anomaly() -> None:
    _seed()
    db = SessionLocal()
    try:
        _set_profile_and_evaluate(
            db,
            asset_id="POP-A-01",
            metrics={
                "link_rtt_ms": 12.0,
                "packet_loss_pct": 0.2,
                "link_up": 1.0,
                "cabinet_temp_c": 28.0,
                "power_draw_w": 180.0 * 1.20,
            },
        )
        drafts = _oi_for_asset(db, "POP-A-01")
        assert any(d.rule_code == "communications_power_anomaly" for d in drafts)
    finally:
        db.close()


def test_oi_condition_warning() -> None:
    _seed()
    db = SessionLocal()
    try:
        _set_profile_and_evaluate(
            db,
            asset_id="POP-A-01",
            metrics={
                "link_rtt_ms": 12.0 + 8.0,
                "packet_loss_pct": 0.2,
                "link_up": 1.0,
                "cabinet_temp_c": 28.0,
                "power_draw_w": 180.0,
            },
        )
        drafts = _oi_for_asset(db, "POP-A-01")
        assert any(d.rule_code == "communications_condition_warning" for d in drafts)
        chosen = next(
            d for d in drafts if d.rule_code == "communications_condition_warning"
        )
        assert chosen.priority == "medium"
        assert "monitoring" in chosen.recommendation.lower()
        assert "check the network" not in chosen.recommendation.lower()
    finally:
        db.close()


def test_oi_source_stable_id_scope_dedup_no_wo() -> None:
    _seed()
    db = SessionLocal()
    try:
        before_wo = db.query(WorkOrder).count()
        _set_profile_and_evaluate(
            db,
            asset_id="POP-C-01",
            metrics={
                "link_rtt_ms": 12.0 + 40.0,
                "packet_loss_pct": 0.2,
                "link_up": 1.0,
                "cabinet_temp_c": 28.0,
                "power_draw_w": 180.0,
            },
        )
        drafts = _oi_for_asset(db, "POP-C-01")
        assert drafts
        chosen = drafts[0]
        assert _infer_recommendation_source(chosen.rule_code) == "telemetry"
        assert chosen.affected_asset == "POP-C-01"
        assert chosen.affected_zone == "zone-c"

        sid1 = OperationalIntelligenceService.stable_id(
            chosen.rule_code, chosen.affected_asset
        )
        sid2 = OperationalIntelligenceService.stable_id(
            chosen.rule_code, chosen.affected_asset
        )
        assert sid1 == sid2
        assert sid1.startswith("rec-")

        # No duplicate rule+asset in generate output
        keys = [f"{d.rule_code}:{d.affected_asset}" for d in drafts]
        assert len(keys) == len(set(keys))

        recs = RecommendationService(db).list_recommendations()
        comm_recs = [r for r in recs if (r.rule_code or "").startswith("communications_")]
        assert any(r.affected_asset == "POP-C-01" for r in comm_recs)
        assert all(
            _infer_recommendation_source(r.rule_code) == "telemetry" for r in comm_recs
        )
        assert db.query(WorkOrder).count() == before_wo
    finally:
        db.close()


def test_unavailable_not_an_alert_code_in_meta() -> None:
    assert "unavailable" not in known_communications_alert_codes()
    assert "power_draw_critical" not in known_communications_alert_codes()
    assert "link_down" in known_communications_alert_codes()


def test_zone_not_used_in_health_severity() -> None:
    metrics = {
        "link_rtt_ms": 40.0,
        "packet_loss_pct": 0.2,
        "link_up": 1.0,
        "cabinet_temp_c": 28.0,
        "power_draw_w": 180.0,
    }
    nominals = {"nominal_link_rtt_ms": 12.0, "nominal_power_draw_w": 180.0}
    results = [
        comm_health.evaluate_asset_condition(
            asset_id="POP-X-01",
            zone_id=zone,
            asset_type="edge_pop",
            metrics=metrics,
            nominals=nominals,
        )
        for zone in ("zone-a", "zone-b", "zone-c", "zone-d")
    ]
    assert len({r.condition for r in results}) == 1
    assert len({tuple(sorted(r.alert_codes)) for r in results}) == 1
