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
