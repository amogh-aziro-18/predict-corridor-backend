"""Shared domain enumerations aligned with frontend contracts."""

from enum import StrEnum


class UtilityKind(StrEnum):
    WATER = "water"
    POWER = "power"
    GAS = "gas"
    ROAD = "road"
    BUILDINGS = "buildings"
    AIRPORT = "airport"
    RAIL = "rail"
    PORT = "port"
    COMMUNICATIONS = "communications"
    RENEWABLES = "renewables"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Trend(StrEnum):
    UP = "up"
    DOWN = "down"
    FLAT = "flat"


class OperationalStatus(StrEnum):
    OPERATIONAL = "operational"
    DEGRADED = "degraded"
    MAINTENANCE = "maintenance"
    OFFLINE = "offline"


class Criticality(StrEnum):
    VERY_HIGH = "very-high"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class BackupLevel(StrEnum):
    REDUNDANT = "redundant"
    PARTIAL = "partial"
    NONE = "none"


class TelemetryQuality(StrEnum):
    GOOD = "good"
    UNCERTAIN = "uncertain"
    BAD = "bad"


class WorkOrderStatus(StrEnum):
    OPEN = "open"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CLOSED = "closed"


class WorkOrderSource(StrEnum):
    RECOMMENDATION = "recommendation"
    MANUAL = "manual"
    PREDICTIVE_MAINTENANCE = "predictive_maintenance"
    SIMULATION = "simulation"


class TechnicianAvailability(StrEnum):
    AVAILABLE = "available"
    ON_TASK = "on_task"
    EN_ROUTE = "en_route"
    OFF_SHIFT = "off_shift"


class IncidentStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class IncidentSource(StrEnum):
    MANUAL = "manual"
    RECOMMENDATION = "recommendation"


class IncidentScope(StrEnum):
    CORRIDOR = "corridor"
    ZONE = "zone"
    ASSET = "asset"


class AlertStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class AlertSource(StrEnum):
    TELEMETRY = "telemetry"
    WEATHER = "weather"
    ENVIRONMENT = "environment"
    RESILIENCE = "resilience"
    MAINTENANCE = "maintenance"
    SCENARIO = "scenario"
    MANUAL = "manual"


class AlertScope(StrEnum):
    CORRIDOR = "corridor"
    ZONE = "zone"
    ASSET = "asset"


class DependencyType(StrEnum):
    """Controlled dependency edge kinds (Phase 16.0 — supplies only)."""

    SUPPLIES = "supplies"
