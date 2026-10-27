"""Incident lifecycle service."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.api.schemas.incident import (
    ALLOWED_STATUS_TRANSITIONS,
    INCIDENT_SEVERITIES,
    IncidentCreate,
    IncidentEventOut,
    IncidentOut,
    IncidentStatusUpdate,
    IncidentUpdate,
)
from app.core.errors import NotFoundError, ValidationAppError
from app.domain.enums import IncidentScope, IncidentSource, IncidentStatus, UtilityKind
from app.domain.models import Incident, IncidentEvent
from app.domain.query import normalize_utility_kind, normalize_zone_id
from app.domain.repositories import (
    AssetRepository,
    IncidentEventRepository,
    IncidentRepository,
    ZoneRepository,
)


def _new_incident_id() -> str:
    return f"INC-{uuid.uuid4().hex[:12]}"


def _new_event_id() -> str:
    return f"INE-{uuid.uuid4().hex[:12]}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class IncidentService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._assets = AssetRepository(db)
        self._zones = ZoneRepository(db)
        self._incidents = IncidentRepository(db)
        self._events = IncidentEventRepository(db)

    def create_incident(self, payload: IncidentCreate) -> IncidentOut:
        self._validate_severity(payload.severity)
        self._validate_source(payload.source)
        scope = self._validate_scope_value(payload.scope)

        zone_id, asset_id, utility_kind = self._resolve_scope(payload, scope)
        recommendation_id, recommendation_snapshot = self._resolve_recommendation(payload)

        if recommendation_id:
            existing = self._incidents.find_active_by_recommendation_id(recommendation_id)
            if existing is not None:
                raise ValidationAppError(
                    f"Active incident already exists for recommendation "
                    f"{recommendation_id}: {existing.id} (status: {existing.status})"
                )

        incident = Incident(
            id=_new_incident_id(),
            title=payload.title.strip(),
            description=payload.description,
            severity=payload.severity,
            status=IncidentStatus.OPEN.value,
            utility_kind=utility_kind,
            scope=scope,
            zone_id=zone_id,
            asset_id=asset_id,
            source=payload.source,
            recommendation_id=recommendation_id,
            recommendation_snapshot=recommendation_snapshot,
            declared_by=payload.declared_by,
        )
        self._incidents.add(incident)
        self._events.add(
            IncidentEvent(
                id=_new_event_id(),
                incident_id=incident.id,
                from_status=None,
                to_status=IncidentStatus.OPEN.value,
                actor=payload.declared_by,
                notes="Incident created",
            )
        )
        self._incidents.save(incident)
        return self.get_incident(incident.id)

    def get_incident(self, incident_id: str) -> IncidentOut:
        incident = self._require_incident(incident_id)
        return self._to_out(incident, include_events=True)

    def list_incidents(
        self,
        *,
        status: str | None = None,
        severity: str | None = None,
        zone_id: str | None = None,
        utility_kind: str | None = None,
        scope: str | None = None,
    ) -> list[IncidentOut]:
        if status:
            self._validate_status_value(status)
        if severity:
            self._validate_severity(severity)
        if scope:
            self._validate_scope_value(scope)
        if utility_kind:
            self._validate_utility_kind(utility_kind)

        rows = self._incidents.list(
            status=status,
            severity=severity,
            zone_id=normalize_zone_id(zone_id),
            utility_kind=normalize_utility_kind(utility_kind),
            scope=scope,
        )
        return [self._to_out(row, include_events=False) for row in rows]

    def update_incident(self, incident_id: str, payload: IncidentUpdate) -> IncidentOut:
        incident = self._require_incident(incident_id)
        if incident.status == IncidentStatus.CLOSED.value:
            raise ValidationAppError("Closed incidents cannot be updated")

        if payload.title is not None:
            incident.title = payload.title.strip()
        if payload.description is not None:
            incident.description = payload.description

        incident.updated_at = _utc_now()
        self._incidents.save(incident)
        return self.get_incident(incident.id)

    def update_incident_status(
        self,
        incident_id: str,
        payload: IncidentStatusUpdate,
    ) -> IncidentOut:
        incident = self._require_incident(incident_id)
        current = incident.status
        target = payload.status

        self._validate_status_value(target)
        allowed = ALLOWED_STATUS_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise ValidationAppError(
                f"Invalid status transition: {current} → {target}. "
                f"Allowed from {current}: {', '.join(sorted(allowed)) or 'none'}."
            )

        if (
            current == IncidentStatus.OPEN.value
            and target == IncidentStatus.CLOSED.value
            and not (payload.notes and payload.notes.strip())
        ):
            raise ValidationAppError(
                "notes are required when dismissing an open incident (open → closed)"
            )

        now = _utc_now()
        if target == IncidentStatus.ACKNOWLEDGED.value and incident.acknowledged_at is None:
            incident.acknowledged_at = now
        if target == IncidentStatus.RESOLVED.value and incident.resolved_at is None:
            incident.resolved_at = now
        if target == IncidentStatus.CLOSED.value and incident.closed_at is None:
            incident.closed_at = now

        incident.status = target
        incident.updated_at = now
        self._events.add(
            IncidentEvent(
                id=_new_event_id(),
                incident_id=incident.id,
                from_status=current,
                to_status=target,
                actor=payload.actor,
                notes=payload.notes,
            )
        )
        self._incidents.save(incident)
        return self.get_incident(incident.id)

    def _resolve_scope(
        self, payload: IncidentCreate, scope: str
    ) -> tuple[str | None, str | None, str]:
        if scope == IncidentScope.CORRIDOR.value:
            if payload.zone_id is not None:
                raise ValidationAppError("corridor scope requires zone_id to be null")
            if payload.asset_id is not None:
                raise ValidationAppError("corridor scope requires asset_id to be null")
            utility_kind = self._require_explicit_utility(payload.utility_kind)
            return None, None, utility_kind

        if scope == IncidentScope.ZONE.value:
            if not payload.zone_id or not str(payload.zone_id).strip():
                raise ValidationAppError("zone scope requires zone_id")
            if payload.asset_id is not None:
                raise ValidationAppError("zone scope requires asset_id to be null")
            zone_id = normalize_zone_id(payload.zone_id)
            if zone_id is None:
                raise ValidationAppError(f"Invalid zone_id: {payload.zone_id}")
            if self._zones.get(zone_id) is None:
                raise NotFoundError(f"Zone not found: {zone_id}")
            utility_kind = self._require_explicit_utility(payload.utility_kind)
            return zone_id, None, utility_kind

        # asset scope
        if not payload.asset_id or not str(payload.asset_id).strip():
            raise ValidationAppError("asset scope requires asset_id")
        asset = self._assets.get(payload.asset_id)
        if asset is None:
            raise NotFoundError(f"Asset not found: {payload.asset_id}")

        # Derive zone/utility from asset; reject conflicting client values
        if payload.zone_id is not None:
            client_zone = normalize_zone_id(payload.zone_id)
            if client_zone is not None and client_zone != asset.zone_id:
                raise ValidationAppError(
                    f"zone_id {payload.zone_id} conflicts with asset zone {asset.zone_id}"
                )
        if payload.utility_kind is not None:
            client_utility = normalize_utility_kind(payload.utility_kind)
            if client_utility is not None and client_utility != asset.utility_kind:
                raise ValidationAppError(
                    f"utility_kind {payload.utility_kind} conflicts with asset "
                    f"utility {asset.utility_kind}"
                )

        return asset.zone_id, asset.id, asset.utility_kind

    def _resolve_recommendation(
        self, payload: IncidentCreate
    ) -> tuple[str | None, dict[str, Any] | None]:
        if payload.source == IncidentSource.RECOMMENDATION.value:
            if not payload.recommendation_id or not payload.recommendation_id.strip():
                raise ValidationAppError(
                    "recommendation_id is required when source=recommendation"
                )
            if payload.recommendation_snapshot is None:
                raise ValidationAppError(
                    "recommendation_snapshot is required when source=recommendation"
                )
            return payload.recommendation_id.strip(), payload.recommendation_snapshot

        if payload.recommendation_id:
            # Allow optional linkage for manual with explicit id+snapshot; no existence store.
            return payload.recommendation_id.strip(), payload.recommendation_snapshot
        return None, payload.recommendation_snapshot

    def _require_explicit_utility(self, utility_kind: str | None) -> str:
        if utility_kind is None or not str(utility_kind).strip():
            raise ValidationAppError(
                "utility_kind is required for corridor and zone incidents"
            )
        return self._validate_utility_kind(utility_kind)

    def _require_incident(self, incident_id: str) -> Incident:
        incident = self._incidents.get(incident_id)
        if incident is None:
            raise NotFoundError(f"Incident not found: {incident_id}")
        return incident

    @staticmethod
    def _validate_severity(severity: str) -> None:
        if severity not in INCIDENT_SEVERITIES:
            raise ValidationAppError(
                f"Invalid severity: {severity}. "
                f"Expected one of: {', '.join(sorted(INCIDENT_SEVERITIES))}."
            )

    @staticmethod
    def _validate_source(source: str) -> None:
        try:
            IncidentSource(source)
        except ValueError as exc:
            raise ValidationAppError(
                f"Invalid source: {source}. "
                f"Expected one of: {', '.join(s.value for s in IncidentSource)}."
            ) from exc

    @staticmethod
    def _validate_scope_value(scope: str) -> str:
        try:
            return IncidentScope(scope).value
        except ValueError as exc:
            raise ValidationAppError(
                f"Invalid scope: {scope}. "
                f"Expected one of: {', '.join(s.value for s in IncidentScope)}."
            ) from exc

    @staticmethod
    def _validate_status_value(status: str) -> None:
        try:
            IncidentStatus(status)
        except ValueError as exc:
            raise ValidationAppError(
                f"Invalid status: {status}. "
                f"Expected one of: {', '.join(s.value for s in IncidentStatus)}."
            ) from exc

    @staticmethod
    def _validate_utility_kind(utility_kind: str) -> str:
        normalized = normalize_utility_kind(utility_kind)
        if normalized is None:
            raise ValidationAppError("utility_kind is required")
        try:
            return UtilityKind(normalized).value
        except ValueError as exc:
            raise ValidationAppError(
                f"Invalid utility_kind: {utility_kind}. "
                f"Expected one of: {', '.join(u.value for u in UtilityKind)}."
            ) from exc

    @staticmethod
    def _to_out(incident: Incident, *, include_events: bool) -> IncidentOut:
        events: list[IncidentEventOut] = []
        if include_events:
            # Prefer loaded relationship; fall back to empty if not loaded
            for event in getattr(incident, "events", []) or []:
                events.append(
                    IncidentEventOut(
                        id=event.id,
                        incident_id=event.incident_id,
                        from_status=event.from_status,
                        to_status=event.to_status,
                        actor=event.actor,
                        notes=event.notes,
                        created_at=event.created_at,
                    )
                )
            events.sort(key=lambda e: e.created_at)
        return IncidentOut(
            id=incident.id,
            title=incident.title,
            description=incident.description,
            severity=incident.severity,
            status=incident.status,
            utility_kind=incident.utility_kind,
            scope=incident.scope,
            zone_id=incident.zone_id,
            asset_id=incident.asset_id,
            source=incident.source,
            recommendation_id=incident.recommendation_id,
            recommendation_snapshot=incident.recommendation_snapshot,
            declared_by=incident.declared_by,
            created_at=incident.created_at,
            updated_at=incident.updated_at,
            acknowledged_at=incident.acknowledged_at,
            resolved_at=incident.resolved_at,
            closed_at=incident.closed_at,
            events=events,
        )
