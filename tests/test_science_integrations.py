from __future__ import annotations

"""
JalSense 2.0 — Science Integrations Unit Tests

Verifies SoilGrids texture depletion logic, Sentinel-1 cloud fallbacks,
and FAO-56 crop coefficient growth stage scaling.
"""

import pytest
from app.utils.crop_data import get_crop_kc
from app.services.satellite import SatelliteResult
from app.services.weather import WeatherForecast
from app.services.stress_engine import predict_stress


def test_get_crop_kc():
    # Wheat: (20, 30, 40, 30, 0.3, 1.15, 0.4)
    # Stage 1 (Initial): 0 - 20 days -> Kc = 0.3
    assert get_crop_kc("wheat", 10) == 0.3
    assert get_crop_kc("wheat", 20) == 0.3

    # Stage 2 (Dev): 21 - 50 days -> Ramps Kc from 0.3 to 1.15
    # Day 35 is midpoint -> Kc should be approx 0.3 + 0.5 * 0.85 = 0.725 -> round-to-even is 0.72
    assert get_crop_kc("wheat", 35) == 0.72
    assert get_crop_kc("wheat", 50) == 1.15


    # Stage 3 (Mid): 51 - 90 days -> Kc = 1.15
    assert get_crop_kc("wheat", 70) == 1.15
    assert get_crop_kc("wheat", 90) == 1.15

    # Stage 4 (Late): 91 - 120 days -> Ramps Kc from 1.15 down to 0.4
    # Day 105 is midpoint -> Kc should be approx 1.15 - 0.5 * 0.75 = 0.775 -> float representation rounds to 0.77
    assert get_crop_kc("wheat", 105) == 0.77
    assert get_crop_kc("wheat", 120) == 0.4


    # Check defaults
    assert get_crop_kc("unknown_crop", 10) == 0.3


def test_soil_texture_base_depletion():
    # Test sand texture (fast drainage, base = 0.05) vs clay texture (slow drainage, base = 0.02)
    # Wheat Kc at day 70 is 1.15
    # Depletion rate = base * Kc
    
    # Sand
    sat_sand = SatelliteResult(
        ndwi=0.1, ndvi=0.5, ndmi=0.0, cloud_cover=5.0, image_date="2026-06-13",
        is_reliable=True, source="live_gee", soil_texture="sand"
    )
    result_sand = predict_stress(sat_sand, None, "wheat", crop_age_days=70)
    # base depletion = 0.05 * 1.15 = 0.0575
    # days_until_stress = 0.1 / 0.0575 = 1.739 -> max(1, 1) = 1 day
    assert result_sand.days_until_stress == 1

    # Clay
    sat_clay = SatelliteResult(
        ndwi=0.1, ndvi=0.5, ndmi=0.0, cloud_cover=5.0, image_date="2026-06-13",
        is_reliable=True, source="live_gee", soil_texture="clay"
    )
    result_clay = predict_stress(sat_clay, None, "wheat", crop_age_days=70)
    # base depletion = 0.02 * 1.15 = 0.023
    # days_until_stress = 0.1 / 0.023 = 4.347 -> max(1, 4) = 4 days
    assert result_clay.days_until_stress == 4


def test_sar_fallback_moisture_level():
    # If Sentinel-2 is cloud-blocked (ndwi is None), test Sentinel-1 SAR moisture fallbacks
    
    # VV < -15.0 -> RED
    sat_red = SatelliteResult(
        ndwi=None, ndvi=None, ndmi=None, cloud_cover=100.0, image_date="2026-06-13",
        is_reliable=False, source="live_gee", s1_vv=-16.2, soil_texture="loam"
    )
    res_red = predict_stress(sat_red, None, "maize", crop_age_days=45)
    assert res_red.level == "red"
    assert res_red.data_mode == "sar_fallback"

    # -15.0 <= VV < -11.0 -> YELLOW
    sat_yellow = SatelliteResult(
        ndwi=None, ndvi=None, ndmi=None, cloud_cover=100.0, image_date="2026-06-13",
        is_reliable=False, source="live_gee", s1_vv=-13.5, soil_texture="loam"
    )
    res_yellow = predict_stress(sat_yellow, None, "maize", crop_age_days=45)
    assert res_yellow.level == "yellow"
    assert res_yellow.data_mode == "sar_fallback"

    # VV >= -11.0 -> GREEN
    sat_green = SatelliteResult(
        ndwi=None, ndvi=None, ndmi=None, cloud_cover=100.0, image_date="2026-06-13",
        is_reliable=False, source="live_gee", s1_vv=-9.5, soil_texture="loam"
    )
    res_green = predict_stress(sat_green, None, "maize", crop_age_days=45)
    assert res_green.level == "green"
    assert res_green.data_mode == "sar_fallback"
