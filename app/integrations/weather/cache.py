"""In-memory TTL cache for external weather responses (Phase 8A POC)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass
class CacheEntry(Generic[T]):
    value: T
    fetched_at: datetime
    expires_at: datetime

    def is_fresh(self, now: datetime | None = None) -> bool:
        moment = now or datetime.now(timezone.utc)
        return moment < self.expires_at


class TtlCache(Generic[T]):
    """Thread-safe bounded TTL cache keyed by string."""

    def __init__(self) -> None:
        self._store: dict[str, CacheEntry[T]] = {}
        self._lock = Lock()

    def get(self, key: str) -> CacheEntry[T] | None:
        with self._lock:
            return self._store.get(key)

    def set(self, key: str, value: T, *, ttl_seconds: int, fetched_at: datetime | None = None) -> None:
        moment = fetched_at or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        expires_at = moment.timestamp() + ttl_seconds
        entry = CacheEntry(
            value=value,
            fetched_at=moment,
            expires_at=datetime.fromtimestamp(expires_at, tz=timezone.utc),
        )
        with self._lock:
            self._store[key] = entry

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def expire_all(self, *, now: datetime | None = None) -> None:
        """Test helper — mark all entries expired."""
        moment = now or datetime.now(timezone.utc)
        with self._lock:
            for key, entry in list(self._store.items()):
                self._store[key] = CacheEntry(
                    value=entry.value,
                    fetched_at=entry.fetched_at,
                    expires_at=moment,
                )


_global_current_cache: TtlCache | None = None
_global_forecast_cache: TtlCache | None = None


def get_current_weather_cache() -> TtlCache:
    global _global_current_cache
    if _global_current_cache is None:
        _global_current_cache = TtlCache()
    return _global_current_cache


def get_forecast_weather_cache() -> TtlCache:
    global _global_forecast_cache
    if _global_forecast_cache is None:
        _global_forecast_cache = TtlCache()
    return _global_forecast_cache


def reset_weather_caches_for_tests() -> None:
    """Test helper — clears module-level weather caches."""
    global _global_current_cache, _global_forecast_cache
    _global_current_cache = TtlCache()
    _global_forecast_cache = TtlCache()
