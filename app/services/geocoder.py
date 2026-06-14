from __future__ import annotations

"""
JalSense 2.0 — Geocoder Service

Converts village names to GPS coordinates using a two-strategy approach:
1. Local CSV (instant, 100% reliable for known villages)
2. Nominatim API (fallback for unknown villages)

The CSV-first strategy ensures demo villages always resolve instantly
without any network dependency.
"""

import csv
import logging
from dataclasses import dataclass
from pathlib import Path

from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

logger = logging.getLogger(__name__)

# Path to the pre-verified villages CSV
_CSV_PATH = Path(__file__).parent.parent.parent / "data" / "villages.csv"


@dataclass
class GeoResult:
    """Geocoding result with provenance tracking."""
    latitude: float
    longitude: float
    resolved_name: str      # Full name: "Chitrakoot, Chitrakoot, Uttar Pradesh"
    district: str | None
    state: str | None
    source: str             # "local_csv" or "nominatim"


class VillageNotFoundError(Exception):
    """Raised when a village name cannot be geocoded by any strategy."""
    def __init__(self, village_name: str):
        self.village_name = village_name
        super().__init__(f"Village not found: '{village_name}'")


# ── Local CSV lookup ──

_csv_data: dict[str, dict] = {}  # Loaded at module import


def _load_csv():
    """Load village coordinates from CSV into memory."""
    global _csv_data
    if not _CSV_PATH.exists():
        logger.warning(f"Villages CSV not found at {_CSV_PATH}")
        return

    with open(_CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Index by lowercase village name for case-insensitive lookup
            key = row["village_name"].strip().lower()
            _csv_data[key] = {
                "village_name": row["village_name"].strip(),
                "district": row["district"].strip(),
                "state": row["state"].strip(),
                "latitude": float(row["latitude"]),
                "longitude": float(row["longitude"]),
            }

    logger.info(f"Loaded {len(_csv_data)} villages from CSV")


# Load on module import
_load_csv()


def _lookup_csv(village_name: str) -> GeoResult | None:
    """Check if village exists in our pre-verified CSV."""
    key = village_name.strip().lower()
    if key in _csv_data:
        v = _csv_data[key]
        return GeoResult(
            latitude=v["latitude"],
            longitude=v["longitude"],
            resolved_name=f"{v['village_name']}, {v['district']}, {v['state']}",
            district=v["district"],
            state=v["state"],
            source="local_csv",
        )
    return None


# ── Nominatim API lookup ──

# Initialize with a descriptive user-agent (Nominatim requires this)
_nominatim = Nominatim(user_agent="jalsense-hackathon-2026", timeout=5)


def _lookup_nominatim(village_name: str) -> GeoResult | None:
    """
    Query OpenStreetMap's Nominatim for village coordinates.
    Appends ", India" for accuracy. Rate limit: 1 req/sec (geopy handles this).
    """
    try:
        query = f"{village_name}, India"
        location = _nominatim.geocode(query)
        if location:
            # Try to extract district/state from display name
            parts = [p.strip() for p in location.address.split(",")]
            district = parts[1] if len(parts) > 2 else None
            state = parts[-2] if len(parts) > 2 else None

            return GeoResult(
                latitude=location.latitude,
                longitude=location.longitude,
                resolved_name=location.address,
                district=district,
                state=state,
                source="nominatim",
            )
    except (GeocoderTimedOut, GeocoderServiceError) as e:
        logger.warning(f"Nominatim failed for '{village_name}': {e}")
    except Exception as e:
        logger.error(f"Unexpected geocoder error for '{village_name}': {e}")

    return None


# ── Public API ──

def geocode_village(village_name: str) -> GeoResult:
    """
    Resolve a village name to coordinates.

    Strategy (ordered by priority):
    1. Local CSV — instant, reliable for demo villages
    2. Nominatim — fallback for unknown villages

    Raises VillageNotFoundError if all strategies fail.
    """
    # Strategy 1: Local CSV
    result = _lookup_csv(village_name)
    if result:
        logger.info(f"Geocoded '{village_name}' via CSV -> ({result.latitude}, {result.longitude})")
        return result

    # Strategy 2: Nominatim
    result = _lookup_nominatim(village_name)
    if result:
        logger.info(f"Geocoded '{village_name}' via Nominatim -> ({result.latitude}, {result.longitude})")
        return result

    # All strategies failed
    logger.warning(f"Could not geocode village: '{village_name}'")
    raise VillageNotFoundError(village_name)
