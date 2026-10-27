"""
Incident Intelligence Service (Phase 11B).

Compose-only situational context for a persistent Incident.
Does not reimplement weather/environment thresholds, resilience posture math,
or recommendation generation.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.api.schemas.incident_intelligence import (
    IncidentAssessmentScopeOut,
    IncidentInputsAvailabilityOut,
    IncidentIntelligenceOut,
    IncidentRecommendedActionOut,
)
from app.core.errors import EnvironmentUnavailableError, NotFoundError, WeatherUnavailableError
from app.domain.enums import IncidentScope, TechnicianAvailability, WorkOrderStatus
from app.domain.repositories import AssetRepository
from app.services.asset_service import AssetService
from app.services.environment_impact_service import EnvironmentImpactService
from app.services.incident_service import IncidentService
from app.services.resilience_assessment_service import ResilienceAssessmentService
from app.services.technician_service import TechnicianService
from app.services.weather_impact_service import WeatherImpactService
from app.services.work_order_service import WorkOrderService

_ASSET_SCOPE_NOTE = (
    "Weather/environment/resilience remain zone-level/centroid-based even for "
    "asset-scoped incidents; the incident asset is returned separately in assets."
)

_CORRIDOR_ZONE_NOTE = (
    "Weather/environment/resilience assessed for the incident's mapped corridor "
    "or zone scope using existing impact services."
)

_NON_CLOSED_WO = (
    WorkOrderStatus.OPEN.value,
    WorkOrderStatus.ASSIGNED.value,
    WorkOrderStatus.IN_PROGRESS.value,
    WorkOrderStatus.COMPLETED.value,
)

_AVAILABILITY_RANK = {
    TechnicianAvailability.AVAILABLE.value: 0,
    TechnicianAvailability.EN_ROUTE.value: 1,
    TechnicianAvailability.ON_TASK.value: 2,
    TechnicianAvailability.OFF_SHIFT.value: 3,
}


class IncidentIntelligenceService:
    """Compose weather/env/resilience + assets/WOs/technicians for one incident."""

    def __init__(
        self,
        db: Session,
        *,
        weather_impact_service: WeatherImpactService | None = None,
        environment_impact_service: EnvironmentImpactService | None = None,
        resilience_assessment_service: ResilienceAssessmentService | None = None,
        incident_service: IncidentService | None = None,
        asset_service: AssetService | None = None,
        work_order_service: WorkOrderService | None = None,
        technician_service: TechnicianService | None = None,
    ) -> None:
        self._db = db
        self._incidents = incident_service or IncidentService(db)
        self._weather = weather_impact_service or WeatherImpactService(db)
        self._environment = environment_impact_service or EnvironmentImpactService(db)
        self._resilience = resilience_assessment_service or ResilienceAssessmentService(
            db,
            weather_impact_service=self._weather,
            environment_impact_service=self._environment,
        )
        self._assets = asset_service or AssetService(db)
        self._asset_repo = AssetRepository(db)
        self._work_orders = work_order_service or WorkOrderService(db)
        self._technicians = technician_service or TechnicianService(db)

    def get_context(self, incident_id: str, *, hours: int = 24) -> IncidentIntelligenceOut:
        hours = max(1, min(int(hours), 168))
        now = datetime.now(timezone.utc)
        incident = self._incidents.get_incident(incident_id)

        assessment_zone_id, utility_kind, scope_note = self._resolve_assessment_scope(incident)

        inputs = IncidentInputsAvailabilityOut()
        weather = None
        try:
            weather = self._weather.assess(zone_id=assessment_zone_id, hours=hours)
            inputs.weather = True
        except WeatherUnavailableError:
            weather = None
            inputs.weather = False

        environment = None
        try:
            environment = self._environment.assess(
                zone_id=assessment_zone_id,
                utility_kind=utility_kind,
                hours=hours,
            )
            inputs.environment = True
        except EnvironmentUnavailableError:
            environment = None
            inputs.environment = False

        # Resilience soft-fails weather/env internally; always attempt assess.
        resilience = self._resilience.assess(
            zone_id=assessment_zone_id,
            utility_kind=utility_kind,
            hours=hours,
            assessed_at=now,
        )
        inputs.resilience = True

        assets = self._load_assets(incident)
        work_orders = self._load_work_orders(incident)
        technicians = self._load_technicians(
            incident_scope=incident.scope,
            prefer_zone_id=assessment_zone_id if incident.scope != IncidentScope.CORRIDOR.value else None,
            asset_zone_id=(
                assessment_zone_id if incident.scope == IncidentScope.ASSET.value else None
            ),
        )

        return IncidentIntelligenceOut(
            incident=incident,
            assessment_scope=IncidentAssessmentScopeOut(
                incident_scope=incident.scope,
                weather_env_resilience_zone_id=assessment_zone_id,
                utility_kind=utility_kind,
                note=scope_note,
            ),
            weather=weather,
            environment=environment,
            resilience=resilience,
            assets=assets,
            work_orders=work_orders,
            technicians=technicians,
            recommended_action=IncidentRecommendedActionOut(),
            inputs_available=inputs,
            assessed_at=now,
        )

    def _resolve_assessment_scope(
        self, incident
    ) -> tuple[str | None, str, str]:
        if incident.scope == IncidentScope.CORRIDOR.value:
            return None, incident.utility_kind, _CORRIDOR_ZONE_NOTE

        if incident.scope == IncidentScope.ZONE.value:
            return incident.zone_id, incident.utility_kind, _CORRIDOR_ZONE_NOTE

        if incident.scope == IncidentScope.ASSET.value:
            if not incident.asset_id:
                raise NotFoundError(f"Incident asset_id missing for asset scope: {incident.id}")
            asset = self._asset_repo.get(incident.asset_id)
            if asset is None:
                raise NotFoundError(f"Asset not found: {incident.asset_id}")
            return asset.zone_id, asset.utility_kind, _ASSET_SCOPE_NOTE

        raise NotFoundError(f"Unsupported incident scope: {incident.scope}")

    def _load_assets(self, incident) -> list:
        if incident.scope == IncidentScope.ASSET.value:
            if not incident.asset_id:
                return []
            try:
                return [self._assets.get_asset(incident.asset_id)]
            except NotFoundError:
                return []

        if incident.scope == IncidentScope.ZONE.value:
            return self._assets.list_assets(
                utility=incident.utility_kind,
                zone=incident.zone_id,
            )

        return self._assets.list_assets(utility=incident.utility_kind)

    def _load_work_orders(self, incident) -> list:
        if incident.scope == IncidentScope.ASSET.value:
            rows = self._work_orders.list_work_orders(asset_id=incident.asset_id)
        elif incident.scope == IncidentScope.ZONE.value:
            rows = self._work_orders.list_work_orders(
                zone_id=incident.zone_id,
                utility=incident.utility_kind,
            )
        else:
            rows = self._work_orders.list_work_orders(utility=incident.utility_kind)

        return [wo for wo in rows if wo.status in _NON_CLOSED_WO]

    def _load_technicians(
        self,
        *,
        incident_scope: str,
        prefer_zone_id: str | None,
        asset_zone_id: str | None,
    ) -> list:
        rows = list(self._technicians.list_technicians())
        preferred_zone = asset_zone_id or prefer_zone_id

        def sort_key(tech) -> tuple:
            certified = 0 if tech.emergency_certified else 1
            zone_match = 0 if preferred_zone and tech.primary_zone_id == preferred_zone else 1
            availability = _AVAILABILITY_RANK.get(tech.availability, 9)
            # Corridor: certified + available first (zone_match unused / all equal)
            if incident_scope == IncidentScope.CORRIDOR.value:
                return (certified, availability, tech.name)
            # Zone / asset: zone match, then certified, then availability
            return (zone_match, certified, availability, tech.name)

        rows.sort(key=sort_key)
        return rows
