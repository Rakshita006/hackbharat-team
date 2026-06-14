from __future__ import annotations

"""
JalSense 2.0 — Weather Service (Open-Meteo)

Fetches 10-day weather forecast for a given lat/lon.
Uses httpx for async HTTP. Cached with 1-hour TTL.
Returns None on failure — stress engine handles gracefully.

Open-Meteo is free, no API key needed, global coverage.
Fair-use limit: ~10,000 requests/day (well above hackathon needs).
"""

import logging
from dataclasses import dataclass

import httpx

from app.config import get_settings
from app.utils.cache import weather_cache, TTLCache

logger = logging.getLogger(__name__)
settings = get_settings()

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


@dataclass
class WeatherForecast:
    """Processed 10-day weather forecast."""
    days_until_rain: int | None     # None if no rain in 10 days
    total_rain_next_7_days: float   # mm cumulative
    max_temp_next_3_days: float     # °C — for heatwave detection
    daily_precip: list[float]       # mm per day, 10 values
    daily_temp_max: list[float]     # °C per day, 10 values
    rain_dates: list[str]           # ISO dates where precip > 2mm
    forecast_dates: list[str]       # All 10 ISO dates


def _parse_response(data: dict) -> WeatherForecast:
    """Parse Open-Meteo JSON response into WeatherForecast."""
    daily = data.get("daily", {})
    dates = daily.get("time", [])
    precip = daily.get("precipitation_sum", [])
    temp_max = daily.get("temperature_2m_max", [])

    # Days until rain (first day with > 2mm precipitation)
    days_until_rain = None
    for i, p in enumerate(precip):
        if p is not None and p > 2.0:
            days_until_rain = i
            break

    # Total rain in next 7 days
    total_rain_7 = sum(p for p in precip[:7] if p is not None)

    # Max temperature in next 3 days
    max_temp_3 = max((t for t in temp_max[:3] if t is not None), default=0.0)

    # Dates with significant rain
    rain_dates = [
        dates[i] for i, p in enumerate(precip)
        if p is not None and p > 2.0
    ]

    return WeatherForecast(
        days_until_rain=days_until_rain,
        total_rain_next_7_days=round(total_rain_7, 1),
        max_temp_next_3_days=round(max_temp_3, 1),
        daily_precip=[round(p, 1) if p is not None else 0.0 for p in precip],
        daily_temp_max=[round(t, 1) if t is not None else 0.0 for t in temp_max],
        rain_dates=rain_dates,
        forecast_dates=dates,
    )


async def get_weather(lat: float, lon: float) -> WeatherForecast | None:
    """
    Fetch 10-day weather forecast for given coordinates.

    Uses 1-hour cache keyed by lat/lon rounded to 0.1° (~11km grid).
    This means farmers in the same village share weather data.

    Returns None on any failure — stress engine handles gracefully.
    """
    # Cache key uses lower precision (0.1°) since weather is regional
    cache_key = f"{round(lat, 1)}_{round(lon, 1)}"

    # Check cache
    cached = weather_cache.get(cache_key)
    if cached:
        logger.info(f"Weather cache HIT for ({lat}, {lon})")
        return cached

    # Fetch from Open-Meteo
    try:
        async with httpx.AsyncClient(timeout=settings.weather_timeout) as client:
            response = await client.get(
                OPEN_METEO_URL,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    "timezone": "Asia/Kolkata",
                    "forecast_days": 10,
                },
            )
            response.raise_for_status()

        forecast = _parse_response(response.json())
        weather_cache.set(cache_key, forecast)

        logger.info(
            f"Weather fetched for ({lat}, {lon}): "
            f"rain_in={forecast.days_until_rain} days, "
            f"max_temp_3d={forecast.max_temp_next_3_days}°C"
        )
        return forecast

    except httpx.TimeoutException:
        logger.warning(f"Weather API timeout ({settings.weather_timeout}s) for ({lat}, {lon})")
    except httpx.HTTPStatusError as e:
        logger.warning(f"Weather API HTTP error for ({lat}, {lon}): {e.response.status_code}")
    except Exception as e:
        logger.error(f"Weather fetch failed for ({lat}, {lon}): {e}")

    return None
