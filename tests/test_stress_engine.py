from __future__ import annotations

"""
JalSense 2.0 — Stress Engine Tests

Tests the stress prediction engine against all combinations of
satellite data, weather data, and edge cases.

Covers:
- Full mode (satellite + weather)
- Weather-only mode (no satellite)
- Unavailable mode (no data at all)
- All threshold boundaries
- Weather modifiers (rain, heatwave)
"""

import pytest
from app.services.satellite import SatelliteResult
from app.services.weather import WeatherForecast
from app.services.stress_engine import predict_stress


def _make_satellite(ndwi: float, ndvi: float = 0.45, ndmi: float = 0.0) -> SatelliteResult:
    """Helper to create a SatelliteResult with sensible defaults."""
    return SatelliteResult(
        ndwi=ndwi, ndvi=ndvi, ndmi=ndmi,
        cloud_cover=10.0, image_date="2026-06-07",
        is_reliable=True, source="test",
    )


def _make_weather(
    days_until_rain: int | None = 5,
    max_temp: float = 35.0,
    total_rain: float = 10.0,
) -> WeatherForecast:
    """Helper to create a WeatherForecast with sensible defaults."""
    precip = [0.0] * 10
    if days_until_rain is not None:
        precip[days_until_rain] = max(5.0, total_rain)

    return WeatherForecast(
        days_until_rain=days_until_rain,
        total_rain_next_7_days=total_rain,
        max_temp_next_3_days=max_temp,
        daily_precip=precip,
        daily_temp_max=[max_temp] * 10,
        rain_dates=[f"2026-06-{10 + days_until_rain}"] if days_until_rain is not None else [],
        forecast_dates=[f"2026-06-{10 + i}" for i in range(10)],
    )


class TestBaseLevel:
    """NDWI threshold → base stress level."""

    def test_green_high_ndwi(self):
        result = predict_stress(_make_satellite(ndwi=0.25), _make_weather(), "wheat")
        assert result.level == "green"
        assert result.data_mode == "full"

    def test_yellow_moderate_ndwi(self):
        result = predict_stress(_make_satellite(ndwi=0.10), _make_weather(), "wheat")
        assert result.level == "yellow"

    def test_red_low_ndwi(self):
        result = predict_stress(_make_satellite(ndwi=-0.05), _make_weather(), "wheat")
        assert result.level == "red"

    def test_boundary_green_yellow(self):
        """NDWI exactly 0.2 should be yellow (not > 0.2)."""
        result = predict_stress(_make_satellite(ndwi=0.2), _make_weather(), "wheat")
        assert result.level == "yellow"

    def test_boundary_yellow_red(self):
        """NDWI exactly 0.0 should be red (not > 0.0)."""
        result = predict_stress(_make_satellite(ndwi=0.0), _make_weather(), "wheat")
        assert result.level == "red"


class TestNDVIEscalation:
    """Low NDVI (damaged crop) escalates stress level."""

    def test_green_to_yellow(self):
        result = predict_stress(
            _make_satellite(ndwi=0.25, ndvi=0.2),  # Green base, but low NDVI
            _make_weather(), "wheat"
        )
        assert result.level == "yellow"

    def test_yellow_to_red(self):
        result = predict_stress(
            _make_satellite(ndwi=0.10, ndvi=0.2),  # Yellow base, low NDVI
            _make_weather(), "wheat"
        )
        assert result.level == "red"


class TestNDMIEscalation:
    """Low NDMI (tissue dehydration) escalates stress level."""

    def test_green_to_yellow(self):
        result = predict_stress(
            _make_satellite(ndwi=0.25, ndmi=-0.15),
            _make_weather(), "wheat"
        )
        assert result.level == "yellow"


class TestWeatherModifiers:
    """Weather-based adjustments to stress level."""

    def test_rain_downgrades_yellow_to_green(self):
        """Rain in ≤3 days should downgrade yellow to green."""
        result = predict_stress(
            _make_satellite(ndwi=0.10),
            _make_weather(days_until_rain=2, total_rain=10.0),
            "wheat"
        )
        assert result.level == "green"
        assert result.rain_advisory is not None

    def test_rain_too_far_no_downgrade(self):
        """Rain in >3 days should NOT downgrade."""
        result = predict_stress(
            _make_satellite(ndwi=0.10),
            _make_weather(days_until_rain=5, total_rain=10.0),
            "wheat"
        )
        assert result.level == "yellow"

    def test_no_rain_escalates_yellow_to_red(self):
        """No rain in 10 days + yellow → escalate to red."""
        result = predict_stress(
            _make_satellite(ndwi=0.10),
            _make_weather(days_until_rain=None),
            "wheat"
        )
        assert result.level == "red"

    def test_heatwave_escalation(self):
        """Max temp > 42°C should escalate by one level."""
        result = predict_stress(
            _make_satellite(ndwi=0.25),  # Green base
            _make_weather(max_temp=43.0),
            "wheat"
        )
        assert result.level == "yellow"  # Green → Yellow due to heatwave


class TestWeatherOnlyMode:
    """Predictions when satellite data is unavailable."""

    def test_no_satellite_with_rain(self):
        result = predict_stress(
            None,
            _make_weather(days_until_rain=2, total_rain=10.0),
            "wheat"
        )
        assert result.level == "green"
        assert result.data_mode == "weather_only"

    def test_no_satellite_no_rain_hot(self):
        result = predict_stress(
            None,
            _make_weather(days_until_rain=None, max_temp=41.0),
            "wheat"
        )
        assert result.level == "red"
        assert result.data_mode == "weather_only"

    def test_no_satellite_moderate_heat(self):
        result = predict_stress(
            None,
            _make_weather(days_until_rain=None, max_temp=37.0),
            "wheat"
        )
        assert result.level == "yellow"
        assert result.data_mode == "weather_only"


class TestUnavailableMode:
    """No data at all."""

    def test_both_none(self):
        result = predict_stress(None, None, "wheat")
        assert result.level == "unknown"
        assert result.data_mode == "unavailable"
        assert result.confidence == "low"


class TestMessageGeneration:
    """Verify that Hindi and English messages are generated."""

    def test_messages_not_empty(self):
        result = predict_stress(_make_satellite(ndwi=0.10), _make_weather(), "wheat")
        assert len(result.alert_message_hindi) > 10
        assert len(result.alert_message_english) > 10

    def test_unavailable_message(self):
        result = predict_stress(None, None, "wheat")
        assert "uplabdh nahi" in result.alert_message_hindi.lower() or "data" in result.alert_message_hindi.lower()
