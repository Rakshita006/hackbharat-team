from __future__ import annotations

"""
JalSense 2.0 — Water Stress Prediction Engine

Combines satellite indices (NDWI, NDVI, NDMI) with weather forecast
to produce an actionable alert for the farmer.

Operates in three modes:
- FULL: Both satellite + weather available (highest confidence)
- WEATHER_ONLY: Satellite unavailable, predictions from weather alone
- UNAVAILABLE: Neither available, sends "will check later" message

Rule-based model (appropriate for Round 1). ML model can replace
the predict() function in Round 2 while keeping the same interface.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.services.satellite import SatelliteResult
from app.services.weather import WeatherForecast
from app.utils.crop_data import get_crop_kc

logger = logging.getLogger(__name__)


@dataclass
class StressResult:
    """Output of the stress prediction engine."""
    level: str                      # "green" | "yellow" | "red" | "unknown"
    days_until_stress: int | None   # Estimated days until NDWI hits critical
    irrigate_by: str | None         # "Wednesday", "Thursday", etc.
    rain_advisory: str | None       # "Rain expected Saturday, hold irrigation"
    alert_message_hindi: str        # Full Hindi message for the farmer
    alert_message_english: str      # For dashboard display
    data_mode: str                  # "full" | "weather_only" | "unavailable"
    confidence: str                 # "high" | "medium" | "low"


# ── Hindi message templates ──

_TEMPLATES = {
    "green": {
        "hindi": (
            "Namaskar! Aapke khet mein pani ki sthiti acchi hai. "
            "Abhi sinchai ki zaroorat nahi hai. "
            "Hum 5 din baad phir check karenge."
        ),
        "english": (
            "Your field has adequate water. No irrigation needed at this time. "
            "We will check again in 5 days."
        ),
    },
    "green_rain": {
        "hindi": (
            "Namaskar! Aapke khet ko sinchai ki zaroorat hai, "
            "lekin {rain_day} ko {rain_mm}mm baarish aane wali hai. "
            "Sinchai rok kar rakhein."
        ),
        "english": (
            "Your field needs water, but rain ({rain_mm}mm) is expected on {rain_day}. "
            "Hold irrigation."
        ),
    },
    "yellow": {
        "hindi": (
            "Namaskar! Aapke khet mein agle {days} din mein pani ki kami ho sakti hai. "
            "{irrigate_by} tak sinchai karein. {rain_note}"
        ),
        "english": (
            "Your field may face water stress in {days} days. "
            "Irrigate by {irrigate_by}. {rain_note}"
        ),
    },
    "red": {
        "hindi": (
            "Namaskar! Aapke khet mein pani ki gambhir kami hai! "
            "Turant sinchai karein. Fasal ko nuksan ho sakta hai."
        ),
        "english": (
            "CRITICAL: Your field has severe water stress! "
            "Irrigate immediately. Crop damage is likely."
        ),
    },
    "weather_only": {
        "hindi": (
            "Namaskar! Badal ke kaaran satellite se aapke khet ki photo saaf nahi mili. "
            "Mausam ke anusaar: {weather_summary}"
        ),
        "english": (
            "Satellite data unavailable due to cloud cover. "
            "Based on weather forecast: {weather_summary}"
        ),
    },
    "unavailable": {
        "hindi": (
            "Namaskar! Abhi aapke khet ka data uplabdh nahi hai. "
            "Hum jald hi dubara check karenge aur aapko batayenge."
        ),
        "english": (
            "Field data is currently unavailable. "
            "We will check again soon and notify you."
        ),
    },
}


def _get_day_name(days_from_now: int) -> str:
    """Get Hindi day name for a date N days from now."""
    target = datetime.now() + timedelta(days=days_from_now)
    day_names = {
        0: "Somwar",    # Monday
        1: "Mangalwar", # Tuesday
        2: "Budhwar",   # Wednesday
        3: "Guruwar",   # Thursday
        4: "Shukrwar",  # Friday
        5: "Shaniwar",  # Saturday
        6: "Raviwar",   # Sunday
    }
    return day_names.get(target.weekday(), target.strftime("%d %B"))


def _get_day_name_english(days_from_now: int) -> str:
    """Get English day name for a date N days from now."""
    target = datetime.now() + timedelta(days=days_from_now)
    return target.strftime("%A, %B %d")


def predict_stress(
    satellite: SatelliteResult | None,
    weather: WeatherForecast | None,
    crop: str,
    crop_age_days: int = 45,
) -> StressResult:
    """
    Run the stress prediction engine.

    Handles three data availability scenarios:
    1. Both satellite + weather → full prediction (high confidence)
    2. Weather only → estimate from temperature + rain (medium confidence)
    3. Neither → "unavailable" message (low confidence)
    """

    # ── Case 3: Neither available ──
    if satellite is None and weather is None:
        return StressResult(
            level="unknown",
            days_until_stress=None,
            irrigate_by=None,
            rain_advisory=None,
            alert_message_hindi=_TEMPLATES["unavailable"]["hindi"],
            alert_message_english=_TEMPLATES["unavailable"]["english"],
            data_mode="unavailable",
            confidence="low",
        )

    # ── Case 2: Weather only ──
    if satellite is None:
        return _predict_weather_only(weather, crop, crop_age_days)

    # ── Case 1: Full prediction (satellite + optional weather) ──
    return _predict_full(satellite, weather, crop, crop_age_days)


def _predict_full(
    sat: SatelliteResult,
    weather: WeatherForecast | None,
    crop: str,
    crop_age_days: int,
) -> StressResult:
    """Full prediction using satellite indices + weather modifiers."""

    # Handle Sentinel-1 cloud fallback if optical (Sentinel-2) data is cloud-blocked
    if sat.ndwi is None:
        if sat.s1_vv is not None:
            return _predict_sar_fallback(sat, weather, crop, crop_age_days)
        else:
            if weather:
                return _predict_weather_only(weather, crop, crop_age_days)
            else:
                return StressResult(
                    level="unknown",
                    days_until_stress=None,
                    irrigate_by=None,
                    rain_advisory=None,
                    alert_message_hindi=_TEMPLATES["unavailable"]["hindi"],
                    alert_message_english=_TEMPLATES["unavailable"]["english"],
                    data_mode="unavailable",
                    confidence="low",
                )

    # Step 1: Base level from NDWI
    if sat.ndwi > 0.2:
        level = "green"
    elif sat.ndwi > 0.0:
        level = "yellow"
    else:
        level = "red"

    # Step 2: NDVI cross-check — if crop is already damaged, escalate
    if sat.ndvi is not None and sat.ndvi < 0.3:
        level = _escalate(level)
        logger.info(f"NDVI={sat.ndvi} < 0.3 → escalated to {level}")

    # Step 3: NDMI refinement — tissue dehydration = escalate
    if sat.ndmi is not None and sat.ndmi < -0.1:
        level = _escalate(level)
        logger.info(f"NDMI={sat.ndmi} < -0.1 → escalated to {level}")

    # Step 4: Weather modifiers (if available)
    rain_advisory = None
    if weather:
        # Rain coming soon + YELLOW → downgrade to GREEN
        if weather.days_until_rain is not None and weather.days_until_rain <= 3 and level == "yellow":
            rain_day = _get_day_name(weather.days_until_rain)
            rain_mm = weather.daily_precip[weather.days_until_rain]
            level = "green"
            rain_advisory = f"{rain_day} ko {rain_mm}mm baarish ki sambhavna hai. Sinchai roken."
            logger.info(f"Rain in {weather.days_until_rain} days → downgraded to green")

        # No rain in 10 days + YELLOW → escalate to RED
        elif weather.days_until_rain is None and level == "yellow":
            level = "red"
            logger.info("No rain in 10 days + yellow → escalated to red")

        # Heatwave: max temp > 42°C → escalate
        if weather.max_temp_next_3_days > 42.0:
            level = _escalate(level)
            logger.info(f"Heatwave {weather.max_temp_next_3_days}°C → escalated to {level}")

    # Step 5: Compute depletion rate using SoilGrids hydrology and FAO-56 crop stages
    base_dep = 0.03
    if sat.soil_texture == "sand":
        base_dep = 0.05
    elif sat.soil_texture == "clay":
        base_dep = 0.02

    kc = get_crop_kc(crop, crop_age_days)
    dep_rate = base_dep * kc
    if weather and weather.max_temp_next_3_days > 35:
        dep_rate += 0.01  # Hot weather evapotranspiration penalty

    days_until_stress = None
    if sat.ndwi is not None and sat.ndwi > 0.0:
        days_until_stress = max(1, int(sat.ndwi / dep_rate))

    # Step 6: Irrigate by date
    irrigate_by = None
    if level in ("yellow", "red") and days_until_stress:
        irrigate_days = max(0, days_until_stress - 2)  # 2 days buffer
        irrigate_by = _get_day_name(irrigate_days)

    # Step 7: Generate messages
    hindi, english = _generate_messages(
        level, days_until_stress, irrigate_by, rain_advisory, weather
    )

    # Append soil hydrology context details
    soil_note_hi = f" (Mitti: {sat.soil_texture}, K_c: {kc})."
    soil_note_en = f" (Soil: {sat.soil_texture}, K_c: {kc})."
    hindi += soil_note_hi
    english += soil_note_en

    return StressResult(
        level=level,
        days_until_stress=days_until_stress,
        irrigate_by=irrigate_by,
        rain_advisory=rain_advisory,
        alert_message_hindi=hindi,
        alert_message_english=english,
        data_mode="full",
        confidence="high" if sat.is_reliable and weather else "medium",
    )


def _predict_sar_fallback(
    sat: SatelliteResult,
    weather: WeatherForecast | None,
    crop: str,
    crop_age_days: int,
) -> StressResult:
    """Predict stress level based on Sentinel-1 SAR Radar (cloud-penetrating)."""
    vv = sat.s1_vv

    # Base level classification from C-band backscatter intensity
    if vv < -15.0:
        level = "red"
    elif vv < -11.0:
        level = "yellow"
    else:
        level = "green"

    rain_advisory = None
    if weather:
        if weather.days_until_rain is not None and weather.days_until_rain <= 3 and level == "yellow":
            rain_day = _get_day_name(weather.days_until_rain)
            rain_mm = weather.daily_precip[weather.days_until_rain]
            level = "green"
            rain_advisory = f"{rain_day} ko {rain_mm}mm baarish ki sambhavna hai. Sinchai roken."
            logger.info(f"SAR Fallback: Rain in {weather.days_until_rain} days → downgraded to green")
        elif weather.days_until_rain is None and level == "yellow":
            level = "red"
            logger.info("SAR Fallback: No rain in 10 days + yellow → escalated red")

    # Hydrologic depletion rate based on SoilGrids & FAO-56 stage
    base_dep = 0.03
    if sat.soil_texture == "sand":
        base_dep = 0.05
    elif sat.soil_texture == "clay":
        base_dep = 0.02

    kc = get_crop_kc(crop, crop_age_days)
    dep_rate = base_dep * kc
    if weather and weather.max_temp_next_3_days > 35:
        dep_rate += 0.01

    # Approximate days until dry using backscatter distance to threshold
    days_until_stress = None
    if level == "yellow":
        db_gap = vv - (-15.0)
        days_until_stress = max(1, int(db_gap / (dep_rate * 150.0)))
    elif level == "green":
        db_gap = vv - (-11.0)
        days_until_stress = max(5, int(db_gap / (dep_rate * 100.0)))

    irrigate_by = None
    if level in ("yellow", "red") and days_until_stress:
        irrigate_days = max(0, days_until_stress - 2)
        irrigate_by = _get_day_name(irrigate_days)

    hindi, english = _generate_messages(
        level, days_until_stress, irrigate_by, rain_advisory, weather
    )

    # Append radar notes to let the farmer know cloud-penetration was utilized
    sar_note_hi = f" (Badal ke kaaran radar data se: VV={vv}dB, mitti={sat.soil_texture})."
    sar_note_en = f" (Cloud-penetrating radar: VV={vv}dB, soil={sat.soil_texture})."
    hindi += sar_note_hi
    english += sar_note_en

    return StressResult(
        level=level,
        days_until_stress=days_until_stress,
        irrigate_by=irrigate_by,
        rain_advisory=rain_advisory,
        alert_message_hindi=hindi,
        alert_message_english=english,
        data_mode="sar_fallback",
        confidence="medium",
    )


def _predict_weather_only(
    weather: WeatherForecast,
    crop: str,
    crop_age_days: int = 45,
) -> StressResult:
    """Estimate stress from weather alone when satellite data is unavailable."""

    # Simple heuristics based on weather
    level = "unknown"
    rain_advisory = None
    kc = get_crop_kc(crop, crop_age_days)
    # Ramps stress speed depending on crop coefficient
    stage_multiplier = 1.0 / max(0.3, kc)
    days_until_stress = None

    if weather.days_until_rain is not None and weather.days_until_rain <= 3:
        # Rain coming soon — probably okay
        level = "green"
        rain_day = _get_day_name(weather.days_until_rain)
        rain_advisory = f"{rain_day} ko baarish aane wali hai."
    elif weather.days_until_rain is None and weather.max_temp_next_3_days > 40:
        # No rain + extreme heat → likely stress
        level = "red"
        days_until_stress = max(1, int(2 * stage_multiplier))
    elif weather.days_until_rain is None and weather.max_temp_next_3_days > 35:
        # No rain + hot → moderate stress likely
        level = "yellow"
        days_until_stress = max(1, int(5 * stage_multiplier))
    elif weather.days_until_rain is not None and weather.days_until_rain > 5:
        # Rain but far away + some heat
        level = "yellow"
        days_until_stress = max(1, int(weather.days_until_rain * stage_multiplier))
    else:
        level = "green"

    # Weather summary for the message
    if weather.days_until_rain is not None:
        rain_info = f"{weather.days_until_rain} din mein baarish ki sambhavna"
    else:
        rain_info = "agle 10 din mein baarish ki sambhavna nahi hai"

    temp_info = f"agle 3 din ka adhiktam taapman {weather.max_temp_next_3_days}°C"
    weather_summary = f"{rain_info}. {temp_info}."

    # Use weather_only template
    hindi = _TEMPLATES["weather_only"]["hindi"].format(weather_summary=weather_summary)
    english = _TEMPLATES["weather_only"]["english"].format(weather_summary=weather_summary)

    # Add action advice based on level
    if level == "red":
        hindi += " Turant sinchai karein."
        english += " Irrigate immediately."
    elif level == "yellow":
        irrigate_by = _get_day_name(max(0, (days_until_stress or 3) - 2))
        hindi += f" {irrigate_by} tak sinchai karein."
        english += f" Irrigate by {_get_day_name_english(max(0, (days_until_stress or 3) - 2))}."

    return StressResult(
        level=level,
        days_until_stress=days_until_stress,
        irrigate_by=_get_day_name(max(0, (days_until_stress or 3) - 2)) if level in ("yellow", "red") else None,
        rain_advisory=rain_advisory,
        alert_message_hindi=hindi,
        alert_message_english=english,
        data_mode="weather_only",
        confidence="low",
    )


def _escalate(level: str) -> str:
    """Escalate stress level by one step: green→yellow→red."""
    if level == "green":
        return "yellow"
    return "red"  # yellow→red, red stays red


def _generate_messages(
    level: str,
    days_until_stress: int | None,
    irrigate_by: str | None,
    rain_advisory: str | None,
    weather: WeatherForecast | None,
) -> tuple[str, str]:
    """Generate Hindi and English alert messages from templates."""

    if level == "green" and rain_advisory:
        # Special case: downgraded to green because of rain
        rain_day = ""
        rain_mm = 0.0
        if weather and weather.days_until_rain is not None:
            rain_day = _get_day_name(weather.days_until_rain)
            rain_mm = weather.daily_precip[weather.days_until_rain]
        hindi = _TEMPLATES["green_rain"]["hindi"].format(rain_day=rain_day, rain_mm=rain_mm)
        english = _TEMPLATES["green_rain"]["english"].format(rain_day=rain_day, rain_mm=rain_mm)
        return hindi, english

    if level == "green":
        return _TEMPLATES["green"]["hindi"], _TEMPLATES["green"]["english"]

    if level == "yellow":
        rain_note = rain_advisory or ""
        rain_note_en = ""
        if weather and weather.days_until_rain is not None:
            rain_day_en = _get_day_name_english(weather.days_until_rain)
            rain_note_en = f"Rain expected {rain_day_en}."

        hindi = _TEMPLATES["yellow"]["hindi"].format(
            days=days_until_stress or "kuch",
            irrigate_by=irrigate_by or "jald",
            rain_note=rain_note,
        )
        english = _TEMPLATES["yellow"]["english"].format(
            days=days_until_stress or "a few",
            irrigate_by=irrigate_by or "soon",
            rain_note=rain_note_en,
        )
        return hindi, english

    if level == "red":
        return _TEMPLATES["red"]["hindi"], _TEMPLATES["red"]["english"]

    # Unknown / fallback
    return _TEMPLATES["unavailable"]["hindi"], _TEMPLATES["unavailable"]["english"]
