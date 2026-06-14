from __future__ import annotations

"""
JalSense 2.0 — Satellite Pipeline (GEE + Sentinel-2)

Three-tier data strategy:
1. In-memory cache (6h TTL) — instant, no network
2. Demo cache file (demo_cache.json) — pre-computed, bulletproof for demos
3. Live GEE call — real satellite data, 3-8 seconds

If all three fail, returns None. The stress engine handles None
by switching to weather-only mode.

GEE authentication supports two modes:
- Interactive (dev): ee.Authenticate() opens browser
- Service account (production): uses JSON key file
"""

import json
import logging
import asyncio
from dataclasses import dataclass, asdict
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import ee

from app.config import get_settings
from app.utils.cache import satellite_cache, TTLCache

logger = logging.getLogger(__name__)
settings = get_settings()

# Thread pool for running blocking GEE calls in async context
_executor = ThreadPoolExecutor(max_workers=4)

# Demo cache loaded from JSON file
_demo_cache: dict[str, dict] = {}

# GEE initialization state
_gee_initialized = False


@dataclass
class SatelliteResult:
    """Result from satellite analysis."""
    ndwi: float | None
    ndvi: float | None
    ndmi: float | None
    cloud_cover: float
    image_date: str
    is_reliable: bool       # False if cloud_cover > 20% or data is stale
    source: str             # "live_gee" | "cache" | "demo_precomputed" | "historical_db"
    
    # Advanced Science Integrations (defaults to maintain demo cache loading compatibility)
    sand_pct: float = 30.0
    clay_pct: float = 30.0
    soil_texture: str = "loam"
    s1_vv: float | None = None
    s1_vh: float | None = None



def init_gee() -> bool:
    """
    Initialize Google Earth Engine connection.
    Returns True if successful, False otherwise.
    """
    global _gee_initialized

    try:
        if settings.gee_service_account_email and Path(settings.gee_key_file_path).exists():
            # Production: service account auth
            credentials = ee.ServiceAccountCredentials(
                settings.gee_service_account_email,
                settings.gee_key_file_path,
            )
            ee.Initialize(credentials, project=settings.gee_project_id)
            logger.info("GEE initialized with service account")
        else:
            # Development: try interactive auth (requires browser)
            ee.Authenticate()
            ee.Initialize(project=settings.gee_project_id)
            logger.info("GEE initialized with interactive auth")

        _gee_initialized = True
        return True

    except Exception as e:
        logger.warning(f"GEE initialization failed: {e}")
        logger.warning("Satellite pipeline will use cached/demo data only")
        _gee_initialized = False
        return False


def load_demo_cache() -> int:
    """
    Load pre-computed satellite data from demo_cache.json.
    Returns the number of entries loaded.
    """
    global _demo_cache
    cache_path = Path(__file__).parent.parent.parent / "data" / "demo_cache.json"

    if not cache_path.exists():
        logger.warning(f"Demo cache not found at {cache_path}")
        return 0

    try:
        with open(cache_path, "r") as f:
            _demo_cache = json.load(f)
        logger.info(f"Loaded {len(_demo_cache)} entries from demo cache")
        return len(_demo_cache)
    except Exception as e:
        logger.error(f"Failed to load demo cache: {e}")
        return 0


def _compute_indices_blocking(lat: float, lon: float) -> SatelliteResult | None:
    """
    Run the GEE computation pipeline. This is a BLOCKING call that
    runs on Google's servers. Typical time: 3-8 seconds.

    Must be called from a thread pool, not directly in async context.
    """
    if not _gee_initialized:
        return None

    try:
        # 1. Create point geometry
        point = ee.Geometry.Point([lon, lat])

        import datetime as dt
        today = dt.date.today()
        date_15_ago = (today - dt.timedelta(days=15)).isoformat()
        date_30_ago = (today - dt.timedelta(days=30)).isoformat()
        date_60_ago = (today - dt.timedelta(days=60)).isoformat()

        # 2. Query ISRIC SoilGrids for soil texture (Sand/Clay percentages)
        sand_pct = 30.0
        clay_pct = 30.0
        soil_texture = "loam"
        try:
            soil_img = ee.Image("projects/soilgrids-isric/composite_250m")
            soil_props = soil_img.select(["sand_0-5cm_mean", "clay_0-5cm_mean"])
            soil_sample = soil_props.sample(point, scale=250).first().getInfo()
            if soil_sample and "properties" in soil_sample:
                s_props = soil_sample["properties"]
                # SoilGrids returns decigrams per kilogram (divide by 10 to get percentage)
                raw_sand = s_props.get("sand_0-5cm_mean", 300)
                raw_clay = s_props.get("clay_0-5cm_mean", 300)
                sand_pct = round(raw_sand / 10.0, 1)
                clay_pct = round(raw_clay / 10.0, 1)
                
                # Simple hydrologic texture proxy
                if sand_pct >= 60.0:
                    soil_texture = "sand"
                elif clay_pct >= 45.0:
                    soil_texture = "clay"
                else:
                    soil_texture = "loam"
        except Exception as e:
            logger.warning(f"Failed to fetch SoilGrids for ({lat}, {lon}): {e}")

        # 3. Query Sentinel-1 SAR Radar (Monsoon cloud penetration fallback)
        s1_vv = None
        s1_vh = None
        try:
            s1_col = (
                ee.ImageCollection("COPERNICUS/S1_GRD")
                .filterBounds(point)
                .filterDate(date_15_ago, today.isoformat())
                .filter(ee.Filter.eq("instrumentMode", "IW"))
                .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
                .sort("system:time_start", False)
            )
            s1_count = s1_col.size().getInfo()
            if s1_count > 0:
                s1_img = s1_col.first()
                s1_sample = s1_img.select(["VV", "VH"]).sample(point, scale=10).first().getInfo()
                if s1_sample and "properties" in s1_sample:
                    s1_props = s1_sample["properties"]
                    s1_vv = round(s1_props.get("VV", -15.0), 2)
                    s1_vh = round(s1_props.get("VH", -22.0), 2)
        except Exception as e:
            logger.warning(f"Failed to fetch Sentinel-1 SAR for ({lat}, {lon}): {e}")

        # 4. Query Sentinel-2 Surface Reflectance Harmonized (Optical Indices)
        filtered = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(point)
            .filterDate(date_30_ago, today.isoformat())
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
            .sort("CLOUDY_PIXEL_PERCENTAGE")
        )

        # 5. Check if any optical images available; expand to 60 days if not
        count = filtered.size().getInfo()
        if count == 0:
            logger.info(f"No images in 30 days for ({lat}, {lon}), expanding to 60 days")
            filtered = (
                ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
                .filterBounds(point)
                .filterDate(date_60_ago, today.isoformat())
                .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 30))  # Relax to 30%
                .sort("CLOUDY_PIXEL_PERCENTAGE")
            )
            count = filtered.size().getInfo()
            if count == 0:
                logger.warning(f"No clean optical images in 60 days for ({lat}, {lon})")
                # Return radar + soil data even if optical is cloud-blocked
                return SatelliteResult(
                    ndwi=None,
                    ndvi=None,
                    ndmi=None,
                    cloud_cover=100.0,
                    image_date="",
                    is_reliable=False,
                    source="live_gee",
                    sand_pct=sand_pct,
                    clay_pct=clay_pct,
                    soil_texture=soil_texture,
                    s1_vv=s1_vv,
                    s1_vh=s1_vh,
                )

        # 6. Get the cleanest optical image
        image = filtered.first()

        # 7. Cloud masking using QA60 band
        qa = image.select("QA60")
        cloud_mask = (
            qa.bitwiseAnd(1 << 10).eq(0)  # No opaque clouds
            .And(qa.bitwiseAnd(1 << 11).eq(0))  # No cirrus clouds
        )
        masked = image.updateMask(cloud_mask)

        # 8. Compute indices
        ndwi_img = masked.normalizedDifference(["B3", "B8"]).rename("NDWI")
        ndvi_img = masked.normalizedDifference(["B8", "B4"]).rename("NDVI")
        ndmi_img = masked.normalizedDifference(["B8", "B11"]).rename("NDMI")

        # 9. Sample at exact point
        indices = ee.Image.cat([ndwi_img, ndvi_img, ndmi_img])
        values = indices.sample(point, scale=10).first().getInfo()

        ndwi = None
        ndvi = None
        ndmi = None
        is_reliable = False

        if values and "properties" in values:
            props = values["properties"]
            ndwi = round(props.get("NDWI", 0.0), 4)
            ndvi = round(props.get("NDVI", 0.0), 4)
            ndmi = round(props.get("NDMI", 0.0), 4)
            is_reliable = True
        else:
            logger.warning(f"No valid pixel data at ({lat}, {lon}) — likely masked by clouds")

        # 10. Get image metadata
        img_info = image.getInfo()
        cloud_pct = img_info["properties"].get("CLOUDY_PIXEL_PERCENTAGE", 0)
        img_date = ""
        # Parse date from system:time_start
        time_start = img_info["properties"].get("system:time_start", 0)
        if time_start:
            import datetime
            img_date = datetime.datetime.fromtimestamp(time_start / 1000).strftime("%Y-%m-%d")

        return SatelliteResult(
            ndwi=ndwi,
            ndvi=ndvi,
            ndmi=ndmi,
            cloud_cover=round(cloud_pct, 1),
            image_date=img_date,
            is_reliable=is_reliable and (cloud_pct < 20),
            source="live_gee",
            sand_pct=sand_pct,
            clay_pct=clay_pct,
            soil_texture=soil_texture,
            s1_vv=s1_vv,
            s1_vh=s1_vh,
        )

    except Exception as e:
        logger.error(f"GEE computation failed for ({lat}, {lon}): {e}")
        return None


async def get_satellite_data(lat: float, lon: float) -> SatelliteResult | None:
    """
    Get satellite indices for a point, using three-tier caching.

    Tier 1: In-memory cache (6h TTL)
    Tier 2: Demo cache file (pre-computed)
    Tier 3: Live GEE call (with timeout)

    Returns None if all tiers fail → stress engine uses weather-only mode.
    """
    cache_key = TTLCache.make_key(lat, lon)

    # Tier 1: In-memory cache
    cached = satellite_cache.get(cache_key)
    if cached:
        logger.info(f"Satellite cache HIT for ({lat}, {lon})")
        return cached

    # Tier 2: Demo cache file
    if cache_key in _demo_cache:
        logger.info(f"Demo cache HIT for ({lat}, {lon})")
        data = _demo_cache[cache_key]
        result = SatelliteResult(**data)
        satellite_cache.set(cache_key, result)  # Promote to in-memory cache
        return result

    # Tier 3: Live GEE call with timeout
    logger.info(f"Cache MISS for ({lat}, {lon}), calling GEE live...")
    try:
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(_executor, _compute_indices_blocking, lat, lon),
            timeout=settings.gee_timeout,
        )
        if result:
            satellite_cache.set(cache_key, result)  # Cache for next time
            logger.info(f"GEE live result for ({lat}, {lon}): NDWI={result.ndwi}")
            return result
    except asyncio.TimeoutError:
        logger.warning(f"GEE timeout ({settings.gee_timeout}s) for ({lat}, {lon})")
    except Exception as e:
        logger.error(f"GEE call failed for ({lat}, {lon}): {e}")

    # All tiers failed
    logger.warning(f"All satellite data sources failed for ({lat}, {lon})")
    return None


def gee_is_connected() -> bool:
    """Check if GEE is initialized (for health endpoint)."""
    return _gee_initialized
