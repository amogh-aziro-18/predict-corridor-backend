"""Shared helpers for zone / utility query normalization."""

from __future__ import annotations


def normalize_zone_id(zone: str | None) -> str | None:
    """Accept ``zone-a``, ``A``, ``a``, ``Zone A`` → ``zone-a``."""
    if zone is None or zone.strip() == "":
        return None
    raw = zone.strip().lower().replace(" ", "")
    if raw.startswith("zone-"):
        return raw
    if raw.startswith("zone") and len(raw) > 4:
        suffix = raw[4:].lstrip("-")
        return f"zone-{suffix}" if suffix else None
    if len(raw) == 1 and raw.isalpha():
        return f"zone-{raw}"
    return raw


def normalize_utility_kind(utility: str | None) -> str | None:
    if utility is None or utility.strip() == "":
        return None
    return utility.strip().lower()
