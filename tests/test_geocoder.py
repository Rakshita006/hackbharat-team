"""
JalSense 2.0 — Geocoder Tests

Tests the geocoding service:
- CSV lookup (known villages)
- Case insensitivity
- Unknown villages (Nominatim fallback would be called in real scenario)
- VillageNotFoundError for truly unknown names
"""

import pytest
from app.services.geocoder import geocode_village, VillageNotFoundError


class TestCSVLookup:
    """Test local CSV geocoding."""

    def test_known_village(self):
        result = geocode_village("Chitrakoot")
        assert abs(result.latitude - 25.1979) < 0.01
        assert abs(result.longitude - 80.8322) < 0.01
        assert result.source == "local_csv"

    def test_case_insensitive(self):
        result = geocode_village("chitrakoot")
        assert result.source == "local_csv"

    def test_another_village(self):
        result = geocode_village("Ranchi")
        assert abs(result.latitude - 23.3441) < 0.01
        assert result.state == "Jharkhand"
        assert result.source == "local_csv"

    def test_vidarbha_village(self):
        result = geocode_village("Yavatmal")
        assert result.state == "Maharashtra"

    def test_unknown_village_raises(self):
        """A completely unknown village should raise VillageNotFoundError."""
        # This test may pass even without Nominatim if the village
        # is truly unknown. Nominatim might find it though.
        with pytest.raises(VillageNotFoundError):
            geocode_village("ZZZUnknownVillageXYZ999")
