from __future__ import annotations

"""
JalSense 2.0 — Application Configuration

Loads all settings from .env file with type validation via Pydantic.
Every setting has a sensible default for development, but GEE and
WhatsApp credentials must be provided for production use.
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    # ── Google Earth Engine ──
    gee_service_account_email: str = ""
    gee_key_file_path: str = "./gee-key.json"
    gee_project_id: str = ""

    # ── WhatsApp Cloud API ──
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_verify_token: str = "jalsense-verify-token"
    whatsapp_app_secret: str = ""


    # ── Database ──
    database_url: str = "sqlite:///./jalsense.db"

    # ── Dashboard Security ──
    dashboard_api_key: str = "jalsense-demo-2026"

    # ── Cache TTLs (seconds) ──
    satellite_cache_ttl: int = Field(default=21600, description="Satellite cache: 6 hours")
    weather_cache_ttl: int = Field(default=3600, description="Weather cache: 1 hour")

    # ── Timeouts (seconds) ──
    gee_timeout: int = Field(default=15, description="GEE request timeout")
    weather_timeout: int = Field(default=10, description="Weather API timeout")
    tts_timeout: int = Field(default=10, description="TTS generation timeout")

    # ── TTS ──
    tts_voice: str = Field(default="hi-IN-SwaraNeural", description="Edge TTS voice for Hindi")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


@lru_cache()
def get_settings() -> Settings:
    """
    Cached settings singleton. Call this instead of constructing Settings()
    directly so the .env file is only read once.
    """
    return Settings()
