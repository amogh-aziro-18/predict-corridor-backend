"""Synthetic POC technician roster — single registry with utility domains.

Fictional demo identities. IDs are stable. Existing Water/Power TEC-* IDs must not change.
``utility_kinds`` associates each technician with one or more utilities
(water, power, gas, and/or communications). Roles are POC demo labels —
not certified credentials.
"""

from __future__ import annotations

from typing import Any

from app.domain.enums import TechnicianAvailability

TECHNICIANS: list[dict[str, Any]] = [
    # --- Water (legacy TEC-* IDs preserved) ---
    {
        "id": "TEC-A-01",
        "name": "Arjun Mehta",
        "role": "Hydraulics Specialist",
        "primary_zone_id": "zone-a",
        "skills": ["hydraulics", "pumps", "valves"],
        "utility_kinds": ["water"],
        "availability": TechnicianAvailability.AVAILABLE.value,
        "emergency_certified": True,
    },
    {
        "id": "TEC-A-02",
        "name": "Neha Rao",
        "role": "Instrumentation Technician",
        "primary_zone_id": "zone-a",
        "skills": ["instrumentation", "scada", "electrical"],
        "utility_kinds": ["water"],
        "availability": TechnicianAvailability.ON_TASK.value,
        "emergency_certified": False,
    },
    {
        "id": "TEC-B-01",
        "name": "Rohan Iyer",
        "role": "Pump Maintenance Technician",
        "primary_zone_id": "zone-b",
        "skills": ["pumps", "hydraulics", "electrical"],
        "utility_kinds": ["water"],
        "availability": TechnicianAvailability.AVAILABLE.value,
        "emergency_certified": True,
    },
    {
        "id": "TEC-B-02",
        "name": "Kavya Nair",
        "role": "Field Operations Technician",
        "primary_zone_id": "zone-b",
        "skills": ["valves", "instrumentation"],
        "utility_kinds": ["water"],
        "availability": TechnicianAvailability.EN_ROUTE.value,
        "emergency_certified": False,
    },
    {
        "id": "TEC-C-01",
        "name": "Vikram Menon",
        "role": "SCADA Technician",
        "primary_zone_id": "zone-c",
        "skills": ["scada", "electrical", "instrumentation"],
        "utility_kinds": ["water"],
        "availability": TechnicianAvailability.AVAILABLE.value,
        "emergency_certified": False,
    },
    {
        "id": "TEC-C-02",
        "name": "Ananya Shah",
        "role": "Hydraulics Crew Lead",
        "primary_zone_id": "zone-c",
        "skills": ["hydraulics", "pumps", "valves"],
        "utility_kinds": ["water"],
        "availability": TechnicianAvailability.ON_TASK.value,
        "emergency_certified": True,
    },
    {
        "id": "TEC-D-01",
        "name": "Rahul Desai",
        "role": "Electrical Technician",
        "primary_zone_id": "zone-d",
        "skills": ["electrical", "instrumentation", "scada"],
        "utility_kinds": ["water"],
        "availability": TechnicianAvailability.OFF_SHIFT.value,
        "emergency_certified": False,
    },
    {
        "id": "TEC-D-02",
        "name": "Priya Kapoor",
        "role": "Valve & Distribution Technician",
        "primary_zone_id": "zone-d",
        "skills": ["valves", "hydraulics"],
        "utility_kinds": ["water"],
        "availability": TechnicianAvailability.AVAILABLE.value,
        "emergency_certified": False,
    },
    # --- Power specialists (new stable IDs) ---
    {
        "id": "TEC-P-01",
        "name": "Sanjay Krishnan",
        "role": "Transformer Maintenance Technician",
        "primary_zone_id": "zone-a",
        "skills": ["transformers", "oil_analysis", "thermal"],
        "utility_kinds": ["power"],
        "availability": TechnicianAvailability.AVAILABLE.value,
        "emergency_certified": True,
    },
    {
        "id": "TEC-P-02",
        "name": "Meera Joshi",
        "role": "Substation Electrical Technician",
        "primary_zone_id": "zone-b",
        "skills": ["substations", "switchgear", "electrical"],
        "utility_kinds": ["power"],
        "availability": TechnicianAvailability.AVAILABLE.value,
        "emergency_certified": True,
    },
    {
        "id": "TEC-P-03",
        "name": "Dev Patel",
        "role": "Power Distribution Technician",
        "primary_zone_id": "zone-c",
        "skills": ["feeders", "distribution", "protection"],
        "utility_kinds": ["power"],
        "availability": TechnicianAvailability.ON_TASK.value,
        "emergency_certified": False,
    },
    {
        "id": "TEC-P-04",
        "name": "Isha Reddy",
        "role": "Protection & Instrumentation Technician",
        "primary_zone_id": "zone-d",
        "skills": ["protection", "instrumentation", "relays"],
        "utility_kinds": ["power"],
        "availability": TechnicianAvailability.AVAILABLE.value,
        "emergency_certified": False,
    },
    # --- Gas specialists (stable TEC-G-* IDs; POC roles, not certified credentials) ---
    {
        "id": "TEC-G-01",
        "name": "Karthik Balan",
        "role": "Gas Systems Technician",
        "primary_zone_id": "zone-a",
        "skills": ["gas_distribution", "pressure_control", "leak_survey"],
        "utility_kinds": ["gas"],
        "availability": TechnicianAvailability.AVAILABLE.value,
        "emergency_certified": True,
    },
    {
        "id": "TEC-G-02",
        "name": "Sneha Varma",
        "role": "Compressor Technician",
        "primary_zone_id": "zone-b",
        "skills": ["compressors", "vibration", "mechanical"],
        "utility_kinds": ["gas"],
        "availability": TechnicianAvailability.AVAILABLE.value,
        "emergency_certified": True,
    },
    {
        "id": "TEC-G-03",
        "name": "Aditya Nambiar",
        "role": "Gas Distribution Technician",
        "primary_zone_id": "zone-c",
        "skills": ["gas_distribution", "regulators", "flow_balance"],
        "utility_kinds": ["gas"],
        "availability": TechnicianAvailability.ON_TASK.value,
        "emergency_certified": False,
    },
    {
        "id": "TEC-G-04",
        "name": "Lakshmi Krishnan",
        "role": "Instrumentation / Valve Technician",
        "primary_zone_id": "zone-d",
        "skills": ["valves", "instrumentation", "actuators"],
        "utility_kinds": ["gas"],
        "availability": TechnicianAvailability.AVAILABLE.value,
        "emergency_certified": False,
    },
    # --- Communications specialists (stable TEC-C-* IDs; POC roles) ---
    {
        "id": "TEC-C-COM-01",
        "name": "Hari Venkatesh",
        "role": "Network / Communications Technician",
        "primary_zone_id": "zone-a",
        "skills": ["edge_pop", "link_diagnostics", "packet_analysis"],
        "utility_kinds": ["communications"],
        "availability": TechnicianAvailability.AVAILABLE.value,
        "emergency_certified": True,
    },
    {
        "id": "TEC-C-COM-02",
        "name": "Fatima Qureshi",
        "role": "Fiber / Link Technician",
        "primary_zone_id": "zone-b",
        "skills": ["fiber", "link_restoration", "path_tracing"],
        "utility_kinds": ["communications"],
        "availability": TechnicianAvailability.AVAILABLE.value,
        "emergency_certified": True,
    },
    {
        "id": "TEC-C-COM-03",
        "name": "Nikhil Agarwal",
        "role": "Communications Infrastructure Technician",
        "primary_zone_id": "zone-c",
        "skills": ["cabinet", "power_draw", "thermal"],
        "utility_kinds": ["communications"],
        "availability": TechnicianAvailability.ON_TASK.value,
        "emergency_certified": False,
    },
    {
        "id": "TEC-C-COM-04",
        "name": "Riya Sengupta",
        "role": "IoT Gateway Technician",
        "primary_zone_id": "zone-d",
        "skills": ["iot_gateway", "field_radio", "edge_devices"],
        "utility_kinds": ["communications"],
        "availability": TechnicianAvailability.AVAILABLE.value,
        "emergency_certified": False,
    },
    # Multi-domain corridor technician (Water + Power + Gas qualification).
    # Communications remains Comm-specialist–qualified for human-in-loop WO path.
    {
        "id": "TEC-M-01",
        "name": "Omar Siddiqui",
        "role": "Multi-Utility Field Technician",
        "primary_zone_id": "zone-a",
        "skills": ["general", "instrumentation", "field_ops"],
        "utility_kinds": ["water", "power", "gas"],
        "availability": TechnicianAvailability.AVAILABLE.value,
        "emergency_certified": True,
    },
]

WATER_TECHNICIAN_IDS: tuple[str, ...] = tuple(
    t["id"] for t in TECHNICIANS if t["utility_kinds"] == ["water"]
)
POWER_TECHNICIAN_IDS: tuple[str, ...] = tuple(
    t["id"] for t in TECHNICIANS if t["utility_kinds"] == ["power"]
)
GAS_TECHNICIAN_IDS: tuple[str, ...] = tuple(
    t["id"] for t in TECHNICIANS if t["utility_kinds"] == ["gas"]
)
COMMUNICATIONS_TECHNICIAN_IDS: tuple[str, ...] = tuple(
    t["id"] for t in TECHNICIANS if t["utility_kinds"] == ["communications"]
)
MULTI_UTILITY_TECHNICIAN_IDS: tuple[str, ...] = tuple(
    t["id"] for t in TECHNICIANS if len(t.get("utility_kinds") or []) > 1
)
