"""In-memory TTL cache for environment observation responses (Phase 9B POC)."""

from __future__ import annotations

from app.integrations.weather.cache import TtlCache

_global_environment_cache: TtlCache | None = None


def get_environment_observation_cache() -> TtlCache:
    global _global_environment_cache
    if _global_environment_cache is None:
        _global_environment_cache = TtlCache()
    return _global_environment_cache


def reset_environment_caches_for_tests() -> None:
    """Test helper — clears module-level environment caches."""
    global _global_environment_cache
    _global_environment_cache = TtlCache()
