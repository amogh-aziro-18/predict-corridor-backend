"""Alert persistence repository."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import AlertStatus
from app.domain.models import Alert

_ACTIVE_STATUSES = (AlertStatus.OPEN.value, AlertStatus.ACKNOWLEDGED.value)


class AlertRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, alert_id: str) -> Alert | None:
        return self._db.get(Alert, alert_id)

    def find_active_by_fingerprint(self, fingerprint: str) -> Alert | None:
        stmt = (
            select(Alert)
            .where(Alert.fingerprint == fingerprint)
            .where(Alert.status.in_(_ACTIVE_STATUSES))
            .limit(1)
        )
        return self._db.scalars(stmt).first()

    def list(
        self,
        *,
        status: str | None = None,
        severity: str | None = None,
        utility_kind: str | None = None,
        zone_id: str | None = None,
        source: str | None = None,
        asset_id: str | None = None,
        active_only: bool = False,
    ) -> list[Alert]:
        stmt = select(Alert).order_by(Alert.raised_at.desc())
        if status:
            stmt = stmt.where(Alert.status == status)
        elif active_only:
            stmt = stmt.where(Alert.status.in_(_ACTIVE_STATUSES))
        if severity:
            stmt = stmt.where(Alert.severity == severity)
        if utility_kind:
            stmt = stmt.where(Alert.utility_kind == utility_kind)
        if zone_id:
            stmt = stmt.where(Alert.zone_id == zone_id)
        if source:
            stmt = stmt.where(Alert.source == source)
        if asset_id:
            stmt = stmt.where(Alert.asset_id == asset_id)
        return list(self._db.scalars(stmt).all())

    def list_active_for_asset(
        self,
        *,
        asset_id: str,
        source: str,
        utility_kind: str,
    ) -> list[Alert]:
        stmt = (
            select(Alert)
            .where(Alert.asset_id == asset_id)
            .where(Alert.source == source)
            .where(Alert.utility_kind == utility_kind)
            .where(Alert.status.in_(_ACTIVE_STATUSES))
        )
        return list(self._db.scalars(stmt).all())

    def add(self, alert: Alert) -> Alert:
        self._db.add(alert)
        self._db.flush()
        return alert

    def save(self, alert: Alert) -> Alert:
        self._db.add(alert)
        self._db.commit()
        self._db.refresh(alert)
        return alert

    def save_many(self) -> None:
        self._db.commit()

    def delete(self, alert: Alert) -> None:
        self._db.delete(alert)
        self._db.commit()
