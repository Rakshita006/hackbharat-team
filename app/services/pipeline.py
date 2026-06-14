from __future__ import annotations

"""
JalSense 2.0 — Pipeline Orchestrator

The single function that ties all services together:
parse → geocode → satellite → weather → stress → TTS → send

Used by both:
- Webhook handler (sends voice note to farmer)
- Demo endpoint (returns all intermediate results as JSON)

Design: Every stage has a try/except with a fallback.
The pipeline ALWAYS reaches "send to farmer" even if upstream services fail.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.farmer import Farmer
from app.models.alert import Alert
from app.utils.message_parser import parse_message, ParsedMessage, ParseError
from app.utils import mask_phone
from app.services.geocoder import geocode_village, GeoResult, VillageNotFoundError
from app.services.satellite import get_satellite_data, SatelliteResult
from app.services.weather import get_weather, WeatherForecast
from app.services.stress_engine import predict_stress, StressResult
from app.services.tts import generate_voice_note
from app.services import whatsapp
from app.services.whatsapp import IncomingMessage

logger = logging.getLogger(__name__)

# ── Hindi response templates for parse errors ──

USAGE_INSTRUCTIONS_HINDI = (
    "Namaskar! Kripya apna gaon aur fasal ka naam bhejein.\n"
    "Jaise: Rampur, gehun\n"
    "Ya: Chitrakoot, dhan"
)

VILLAGE_NOT_FOUND_HINDI = (
    "Maaf kijiye, aapka gaon nahi mila. "
    "Kripya district ke saath bhejein.\n"
    "Jaise: Rampur, Rampur District"
)

NON_TEXT_HINDI = (
    "Kripya apna gaon aur fasal ka naam text mein bhejein.\n"
    "Jaise: Rampur, gehun"
)

CROP_NOT_FOUND_HINDI = (
    "Kripya apni fasal ka naam bhejein.\n"
    "Jaise: Rampur, gehun\n"
    "Fasal: gehun, dhan, makka, chana, sarson, kapas, soybean, arhar"
)


@dataclass
class PipelineResult:
    """Complete result from a pipeline run (for demo endpoint JSON response)."""
    success: bool = False
    error: str | None = None
    processing_time_seconds: float = 0.0

    # Input
    phone: str = ""
    raw_message: str = ""
    village: str | None = None
    crop: str | None = None

    # Geocoding
    geo: dict | None = None

    # Satellite
    satellite: dict | None = None

    # Weather
    weather: dict | None = None

    # Stress prediction
    stress: dict | None = None

    # Alert
    alert_hindi: str | None = None
    alert_english: str | None = None

    def to_demo_response(self) -> dict:
        """Format for the /api/demo JSON response."""
        return {
            "success": self.success,
            "error": self.error,
            "processing_time_seconds": round(self.processing_time_seconds, 2),
            "input": {
                "village": self.village,
                "crop": self.crop,
            },
            "geocoding": self.geo,
            "satellite": self.satellite,
            "weather": self.weather,
            "stress_prediction": self.stress,
            "alert": {
                "hindi": self.alert_hindi,
                "english": self.alert_english,
            },
        }


async def run_full_pipeline(message: IncomingMessage, crop_age_days: int = 45) -> PipelineResult:
    """
    Complete pipeline: parse → geocode → satellite → weather → stress → TTS → send.

    Every stage has a try/except with a fallback. The pipeline never crashes.
    The farmer always gets either a voice note or a text message.
    """
    start_time = time.time()
    result = PipelineResult(phone=message.phone, raw_message=message.text)

    # ── Stage 1: Parse message ──
    parsed = parse_message(message.text)

    if isinstance(parsed, ParseError):
        logger.info(f"Parse failed for '{message.text}': {parsed.reason}")
        if parsed.reason == "no_crop":
            await whatsapp.send_text_message(message.phone, CROP_NOT_FOUND_HINDI)
        elif parsed.reason == "no_village":
            await whatsapp.send_text_message(message.phone, USAGE_INSTRUCTIONS_HINDI)
        else:
            await whatsapp.send_text_message(message.phone, USAGE_INSTRUCTIONS_HINDI)
        result.error = f"parse_failed: {parsed.reason}"
        result.processing_time_seconds = time.time() - start_time
        return result

    result.village = parsed.village
    result.crop = parsed.crop
    logger.info(f"Parsed: village='{parsed.village}', crop='{parsed.crop}'")

    # ── Stage 2: Geocode ──
    try:
        geo = geocode_village(parsed.village)
        result.geo = {
            "latitude": geo.latitude,
            "longitude": geo.longitude,
            "resolved_name": geo.resolved_name,
            "district": geo.district,
            "state": geo.state,
            "source": geo.source,
        }
        logger.info(f"Geocoded: ({geo.latitude}, {geo.longitude}) via {geo.source}")
    except VillageNotFoundError:
        await whatsapp.send_text_message(message.phone, VILLAGE_NOT_FOUND_HINDI)
        result.error = "geocode_failed"
        result.processing_time_seconds = time.time() - start_time
        return result

    # ── Stage 3+4: Satellite + Weather (PARALLEL) ──
    satellite_data, weather_data = await asyncio.gather(
        _safe_get_satellite(geo.latitude, geo.longitude),
        _safe_get_weather(geo.latitude, geo.longitude),
    )

    if satellite_data:
        result.satellite = {
            "ndwi": satellite_data.ndwi,
            "ndvi": satellite_data.ndvi,
            "ndmi": satellite_data.ndmi,
            "cloud_cover": satellite_data.cloud_cover,
            "image_date": satellite_data.image_date,
            "is_reliable": satellite_data.is_reliable,
            "source": satellite_data.source,
        }

    if weather_data:
        result.weather = {
            "days_until_rain": weather_data.days_until_rain,
            "total_rain_7_days": weather_data.total_rain_next_7_days,
            "max_temp_3_days": weather_data.max_temp_next_3_days,
            "rain_dates": weather_data.rain_dates,
        }

    # ── Stage 5: Stress prediction ──
    stress = predict_stress(satellite_data, weather_data, parsed.crop, crop_age_days)
    result.stress = {
        "level": stress.level,
        "days_until_stress": stress.days_until_stress,
        "irrigate_by": stress.irrigate_by,
        "rain_advisory": stress.rain_advisory,
        "data_mode": stress.data_mode,
        "confidence": stress.confidence,
    }
    result.alert_hindi = stress.alert_message_hindi
    result.alert_english = stress.alert_message_english

    logger.info(
        f"Stress prediction: {stress.level} (mode={stress.data_mode}, "
        f"confidence={stress.confidence})"
    )

    # ── Stage 6: Generate voice note ──
    audio_path = await generate_voice_note(stress.alert_message_hindi)

    # ── Stage 7: Send to farmer ──
    if audio_path:
        success = await whatsapp.send_voice_note(message.phone, audio_path)
        if not success:
            # Voice note failed — fall back to text
            await whatsapp.send_text_message(message.phone, stress.alert_message_hindi)
    else:
        # TTS failed — send text directly
        logger.warning("TTS failed, falling back to text message")
        await whatsapp.send_text_message(message.phone, stress.alert_message_hindi)

    # ── Stage 8: Save to database ──
    try:
        _save_to_db(message, parsed, geo, satellite_data, weather_data, stress)
    except Exception as e:
        # DB failure is non-fatal — farmer already got their alert
        logger.error(f"Database save failed: {e}")

    result.success = True
    result.processing_time_seconds = time.time() - start_time

    logger.info(
        f"Pipeline complete for {mask_phone(message.phone)}: "
        f"level={stress.level}, time={result.processing_time_seconds:.1f}s"
    )

    return result


async def run_demo_pipeline(village: str, crop: str, crop_age_days: int = 45) -> PipelineResult:
    """
    Run the pipeline for the demo endpoint (no WhatsApp sending).
    Returns all intermediate results for the demo panel to display.
    """
    start_time = time.time()
    result = PipelineResult(village=village, crop=crop)

    # Geocode
    try:
        geo = geocode_village(village)
        result.geo = {
            "latitude": geo.latitude,
            "longitude": geo.longitude,
            "resolved_name": geo.resolved_name,
            "district": geo.district,
            "state": geo.state,
            "source": geo.source,
        }
    except VillageNotFoundError:
        result.error = f"Village '{village}' not found"
        result.processing_time_seconds = time.time() - start_time
        return result

    # Satellite + Weather (parallel)
    satellite_data, weather_data = await asyncio.gather(
        _safe_get_satellite(geo.latitude, geo.longitude),
        _safe_get_weather(geo.latitude, geo.longitude),
    )

    if satellite_data:
        result.satellite = {
            "ndwi": satellite_data.ndwi,
            "ndvi": satellite_data.ndvi,
            "ndmi": satellite_data.ndmi,
            "cloud_cover": satellite_data.cloud_cover,
            "image_date": satellite_data.image_date,
            "is_reliable": satellite_data.is_reliable,
            "source": satellite_data.source,
            "interpretation": {
                "ndwi": _interpret_ndwi(satellite_data.ndwi),
                "ndvi": _interpret_ndvi(satellite_data.ndvi),
                "ndmi": _interpret_ndmi(satellite_data.ndmi),
            },
        }

    if weather_data:
        result.weather = {
            "days_until_rain": weather_data.days_until_rain,
            "total_rain_7_days": weather_data.total_rain_next_7_days,
            "max_temp_3_days": weather_data.max_temp_next_3_days,
            "rain_dates": weather_data.rain_dates,
            "daily_forecast": [
                {
                    "date": weather_data.forecast_dates[i],
                    "precip_mm": weather_data.daily_precip[i],
                    "temp_max": weather_data.daily_temp_max[i],
                }
                for i in range(len(weather_data.forecast_dates))
            ],
        }

    # Stress prediction
    stress = predict_stress(satellite_data, weather_data, crop, crop_age_days)
    result.stress = {
        "level": stress.level,
        "days_until_stress": stress.days_until_stress,
        "irrigate_by": stress.irrigate_by,
        "rain_advisory": stress.rain_advisory,
        "data_mode": stress.data_mode,
        "confidence": stress.confidence,
    }
    result.alert_hindi = stress.alert_message_hindi
    result.alert_english = stress.alert_message_english

    result.success = True
    result.processing_time_seconds = time.time() - start_time
    return result


# ── Helpers ──

async def _safe_get_satellite(lat: float, lon: float) -> SatelliteResult | None:
    """Wrapper that catches all satellite errors and returns None."""
    try:
        return await get_satellite_data(lat, lon)
    except Exception as e:
        logger.error(f"Satellite service error: {e}")
        return None


async def _safe_get_weather(lat: float, lon: float) -> WeatherForecast | None:
    """Wrapper that catches all weather errors and returns None."""
    try:
        return await get_weather(lat, lon)
    except Exception as e:
        logger.error(f"Weather service error: {e}")
        return None


def _save_to_db(
    message: IncomingMessage,
    parsed: ParsedMessage,
    geo: GeoResult,
    satellite: SatelliteResult | None,
    weather: WeatherForecast | None,
    stress: StressResult,
) -> None:
    """Save farmer and alert records to database."""
    db: Session = SessionLocal()
    try:
        # Upsert farmer
        farmer = db.query(Farmer).filter(Farmer.phone_number == message.phone).first()
        if farmer:
            # Update existing farmer
            farmer.village_name = parsed.village
            farmer.crop_name = parsed.crop
            farmer.latitude = geo.latitude
            farmer.longitude = geo.longitude
            farmer.district = geo.district
            farmer.state = geo.state
            farmer.current_stress_level = stress.level
            farmer.last_alert_sent_at = datetime.now(timezone.utc)
            if message.sender_name:
                farmer.farmer_name = message.sender_name
        else:
            # Create new farmer
            farmer = Farmer(
                phone_number=message.phone,
                farmer_name=message.sender_name or None,
                village_name=parsed.village,
                district=geo.district,
                state=geo.state,
                crop_name=parsed.crop,
                latitude=geo.latitude,
                longitude=geo.longitude,
                current_stress_level=stress.level,
                last_alert_sent_at=datetime.now(timezone.utc),
            )
            db.add(farmer)
            db.flush()  # Get farmer.id for the alert FK

        # Create alert record
        weather_summary = ""
        if weather:
            rain_info = (
                f"Rain in {weather.days_until_rain} days"
                if weather.days_until_rain is not None
                else "No rain in 10 days"
            )
            weather_summary = (
                f"{rain_info}. "
                f"Max temp (3d): {weather.max_temp_next_3_days}°C. "
                f"Total rain (7d): {weather.total_rain_next_7_days}mm."
            )

        alert = Alert(
            farmer_id=farmer.id,
            ndwi=satellite.ndwi if satellite else None,
            ndvi=satellite.ndvi if satellite else None,
            ndmi=satellite.ndmi if satellite else None,
            cloud_cover=satellite.cloud_cover if satellite else None,
            stress_level=stress.level,
            days_until_stress=stress.days_until_stress,
            weather_summary=weather_summary,
            alert_message_hindi=stress.alert_message_hindi,
            alert_message_english=stress.alert_message_english,
            satellite_image_date=satellite.image_date if satellite else None,
            was_reliable=(satellite.is_reliable if satellite else False),
            data_source=stress.data_mode,
        )
        db.add(alert)

        db.commit()
        logger.info(f"Saved to DB: farmer_id={farmer.id}, alert stress={stress.level}")

    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


def _interpret_ndwi(ndwi: float) -> str:
    if ndwi > 0.3: return "Standing water / waterlogged"
    if ndwi > 0.1: return "Adequate soil moisture"
    if ndwi > 0.0: return "Moderate water deficit"
    return "Dry soil, water stress"

def _interpret_ndvi(ndvi: float) -> str:
    if ndvi > 0.6: return "Dense healthy vegetation"
    if ndvi > 0.3: return "Moderate vegetation / growing crop"
    if ndvi > 0.1: return "Sparse vegetation / stressed crop"
    return "Bare soil / dead crop"

def _interpret_ndmi(ndmi: float) -> str:
    if ndmi > 0.2: return "Well-hydrated vegetation"
    if ndmi > 0.0: return "Mild moisture stress"
    if ndmi > -0.2: return "Moderate tissue dehydration"
    return "Severe dehydration, crop at risk"
