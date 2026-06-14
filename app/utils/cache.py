from __future__ import annotations

"""
JalSense 2.0 — In-Memory TTL Cache

Simple dict-based cache with per-key expiry. No Redis dependency needed
at hackathon scale. Two global instances are created:

- satellite_cache (6-hour TTL): GEE results don't change faster than this
- weather_cache   (1-hour TTL): Weather updates more frequently

Cache key convention: f"{round(lat,2)}_{round(lon,2)}" which groups
points within ~1.1 km into the same cache bucket.
"""

import time
import threading
from typing import Any

from app.config import get_settings


class TTLCache:
    """Thread-safe in-memory cache with per-key time-to-live."""

    def __init__(self, default_ttl: int = 3600):
        self._store: dict[str, tuple[Any, float]] = {}
        self._default_ttl = default_ttl
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        """Return cached value if it exists and hasn't expired, else None."""
        with self._lock:
            if key in self._store:
                value, expires_at = self._store[key]
                if time.time() < expires_at:
                    return value
                # Expired — clean it up
                del self._store[key]
            return None

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Store a value with optional custom TTL (defaults to instance TTL)."""
        with self._lock:
            self._store[key] = (value, time.time() + (ttl or self._default_ttl))

    def clear(self) -> None:
        """Remove all entries."""
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        """Number of entries (including potentially expired ones)."""
        return len(self._store)

    @staticmethod
    def make_key(lat: float, lon: float) -> str:
        """
        Create a cache key from coordinates.
        Rounds to 2 decimal places (~1.1 km resolution at equator).
        All points within the same ~1 km² share one cache entry.
        """
        return f"{round(lat, 2)}_{round(lon, 2)}"


# ── Global cache instances ──

_settings = get_settings()

satellite_cache = TTLCache(default_ttl=_settings.satellite_cache_ttl)
weather_cache = TTLCache(default_ttl=_settings.weather_cache_ttl)
