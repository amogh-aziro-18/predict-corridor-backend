"""Alert lifecycle service — utility-agnostic attention records."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session

from app.api.schemas.alert import (
    ALLOWED_STATUS_TRANSITIONS,
    ALERT_SEVERITIES,
    AlertOut,
    AlertStatusUpdate,
)
from app.core.errors import NotFoundError, ValidationAppError
from app.domain.enums import AlertScope, AlertSource, AlertStatus
from app.domain.models import Alert
from app.domain.query import normalize_utility_kind, normalize_zone_id
from app.domain.repositories.alert_repository import AlertRepository

# Present-code → (title, description, severity)
AlertCodeMeta = tuple[str, str | None, str]


def build_alert_fingerprint(
    *,
    source: str,
    alert_code: str,
    scope_key: str,
    utility_kind: str,
) -> str:
    raw = f"{source}|{alert_code}|{scope_key}|{utility_kind}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def scope_key_for(*, scope: str, zone_id: str | None, asset_id: str | None) -> str:
    if scope == AlertScope.ASSET.value:
        if not asset_id:
            raise ValidationAppError("asset scope requires asset_id for fingerprint")
        return f"asset:{asset_id}"
    if scope == AlertScope.ZONE.value:
        if not zone_id:
            raise ValidationAppError("zone scope requires zone_id for fingerprint")
        return f"zone:{zone_id}"
    return "corridor"


def _new_alert_id() -> str:
    return f"ALR-{uuid.uuid4().hex[:12]}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AlertService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._alerts = AlertRepository(db)

    def get_alert(self, alert_id: str) -> AlertOut:
        return self._to_out(self._require(alert_id))

    def list_alerts(
        self,
        *,
        status: str | None = None,
        severity: str | None = None,
        utility_kind: str | None = None,
        zone_id: str | None = None,
        source: str | None = None,
    ) -> list[AlertOut]:
        if status:
            self._validate_status(status)
        if severity:
            self._validate_severity(severity)
        if source:
            self._validate_source(source)

        rows = self._alerts.list(
            status=status,
            severity=severity,
            utility_kind=normalize_utility_kind(utility_kind),
            zone_id=normalize_zone_id(zone_id),
            source=source,
        )
        return [self._to_out(row) for row in rows]

    def update_status(self, alert_id: str, payload: AlertStatusUpdate) -> AlertOut:
        alert = self._require(alert_id)
        current = alert.status
        target = payload.status
        self._validate_status(target)
        allowed = ALLOWED_STATUS_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise ValidationAppError(
                f"Invalid status transition: {current} → {target}. "
                f"Allowed from {current}: {', '.join(sorted(allowed)) or 'none'}."
            )

        now = _utc_now()
        if target == AlertStatus.ACKNOWLEDGED.value:
            if alert.acknowledged_at is None:
                alert.acknowledged_at = now
            if payload.notes is not None:
                alert.ack_notes = payload.notes
        if target == AlertStatus.RESOLVED.value:
            if alert.resolved_at is None:
                alert.resolved_at = now
            if payload.notes is not None:
                alert.resolve_notes = payload.notes

        alert.status = target
        alert.updated_at = now
        self._alerts.save(alert)
        return self._to_out(alert)

    def upsert_condition(
        self,
        *,
        source: str,
        alert_code: str,
        title: str,
        description: str | None,
        severity: str,
        utility_kind: str,
        scope: str,
        zone_id: str | None = None,
        asset_id: str | None = None,
        recommendation_id: str | None = None,
        commit: bool = True,
    ) -> Alert:
        """Create or update an active alert for the fingerprint. Utility-agnostic."""
        self._validate_source(source)
        self._validate_severity(severity)
        self._validate_scope(scope)
        utility = normalize_utility_kind(utility_kind) or utility_kind
        zone = normalize_zone_id(zone_id)
        key = scope_key_for(scope=scope, zone_id=zone, asset_id=asset_id)
        fingerprint = build_alert_fingerprint(
            source=source,
            alert_code=alert_code,
            scope_key=key,
            utility_kind=utility,
        )

        existing = self._alerts.find_active_by_fingerprint(fingerprint)
        now = _utc_now()
        if existing is not None:
            changed = False
            if existing.severity != severity:
                existing.severity = severity
                changed = True
            if existing.title != title:
                existing.title = title
                changed = True
            if description is not None and existing.description != description:
                existing.description = description
                changed = True
            if changed:
                existing.updated_at = now
                if commit:
                    self._alerts.save(existing)
                else:
                    self._alerts.add(existing)
            return existing

        alert = Alert(
            id=_new_alert_id(),
            fingerprint=fingerprint,
            title=title,
            description=description,
            severity=severity,
            status=AlertStatus.OPEN.value,
            source=source,
            alert_code=alert_code,
            utility_kind=utility,
            scope=scope,
            zone_id=zone,
            asset_id=asset_id,
            recommendation_id=recommendation_id,
            raised_at=now,
            updated_at=now,
        )
        self._alerts.add(alert)
        if commit:
            self._alerts.save(alert)
        return alert

    def sync_condition_set(
        self,
        *,
        source: str,
        utility_kind: str,
        scope: str,
        zone_id: str | None,
        asset_id: str | None,
        present_codes: set[str],
        meta_for_code: Callable[[str], AlertCodeMeta],
        resolve_notes: str = "Condition cleared",
    ) -> None:
        """
        Upsert alerts for present codes; resolve active alerts whose codes are gone.

        Scoped to one asset (or zone) evaluation set — not a global sweep.
        """
        utility = normalize_utility_kind(utility_kind) or utility_kind
        zone = normalize_zone_id(zone_id)

        for code in present_codes:
            title, description, severity = meta_for_code(code)
            self.upsert_condition(
                source=source,
                alert_code=code,
                title=title,
                description=description,
                severity=severity,
                utility_kind=utility,
                scope=scope,
                zone_id=zone,
                asset_id=asset_id,
                commit=False,
            )

        if asset_id:
            active = self._alerts.list_active_for_asset(
                asset_id=asset_id,
                source=source,
                utility_kind=utility,
            )
        else:
            active = [
                a
                for a in self._alerts.list(
                    source=source,
                    utility_kind=utility,
                    zone_id=zone,
                    active_only=True,
                )
                if a.scope == scope
            ]

        now = _utc_now()
        for alert in active:
            if alert.alert_code not in present_codes:
                alert.status = AlertStatus.RESOLVED.value
                if alert.resolved_at is None:
                    alert.resolved_at = now
                alert.resolve_notes = resolve_notes
                alert.updated_at = now
                self._alerts.add(alert)

        self._db.flush()

    def _require(self, alert_id: str) -> Alert:
        alert = self._alerts.get(alert_id)
        if alert is None:
            raise NotFoundError(f"Alert not found: {alert_id}")
        return alert

    @staticmethod
    def _validate_status(status: str) -> None:
        try:
            AlertStatus(status)
        except ValueError as exc:
            raise ValidationAppError(
                f"Invalid status: {status}. "
                f"Expected one of: {', '.join(s.value for s in AlertStatus)}."
            ) from exc

    @staticmethod
    def _validate_severity(severity: str) -> None:
        if severity not in ALERT_SEVERITIES:
            raise ValidationAppError(
                f"Invalid severity: {severity}. "
                f"Expected one of: {', '.join(sorted(ALERT_SEVERITIES))}."
            )

    @staticmethod
    def _validate_source(source: str) -> None:
        try:
            AlertSource(source)
        except ValueError as exc:
            raise ValidationAppError(
                f"Invalid source: {source}. "
                f"Expected one of: {', '.join(s.value for s in AlertSource)}."
            ) from exc

    @staticmethod
    def _validate_scope(scope: str) -> None:
        try:
            AlertScope(scope)
        except ValueError as exc:
            raise ValidationAppError(
                f"Invalid scope: {scope}. "
                f"Expected one of: {', '.join(s.value for s in AlertScope)}."
            ) from exc

    @staticmethod
    def _to_out(alert: Alert) -> AlertOut:
        return AlertOut(
            id=alert.id,
            fingerprint=alert.fingerprint,
            title=alert.title,
            description=alert.description,
            severity=alert.severity,
            status=alert.status,
            source=alert.source,
            alert_code=alert.alert_code,
            utility_kind=alert.utility_kind,
            scope=alert.scope,
            zone_id=alert.zone_id,
            asset_id=alert.asset_id,
            recommendation_id=alert.recommendation_id,
            raised_at=alert.raised_at,
            updated_at=alert.updated_at,
            acknowledged_at=alert.acknowledged_at,
            resolved_at=alert.resolved_at,
            ack_notes=alert.ack_notes,
            resolve_notes=alert.resolve_notes,
        )
