"""
JalSense 2.0 — Message Parser Tests

Tests the message parser against real-world farmer input patterns:
- Comma-separated (common)
- Space-separated
- Multi-word village names
- Devanagari script
- Misspelled crop names
- Reverse order (crop, village)
- Edge cases (empty, gibberish, single word)
"""

import pytest
from app.utils.message_parser import parse_message, ParsedMessage, ParseError


class TestCommaSeparated:
    """Standard "Village, Crop" format."""

    def test_basic(self):
        result = parse_message("Chitrakoot, gehun")
        assert isinstance(result, ParsedMessage)
        assert result.village == "Chitrakoot"
        assert result.crop == "wheat"

    def test_lowercase(self):
        result = parse_message("chitrakoot, gehun")
        assert isinstance(result, ParsedMessage)
        assert result.village == "Chitrakoot"
        assert result.crop == "wheat"

    def test_uppercase(self):
        result = parse_message("CHITRAKOOT, WHEAT")
        assert isinstance(result, ParsedMessage)
        assert result.village == "Chitrakoot"
        assert result.crop == "wheat"

    def test_english_crop(self):
        result = parse_message("Rampur, wheat")
        assert isinstance(result, ParsedMessage)
        assert result.village == "Rampur"
        assert result.crop == "wheat"

    def test_rice(self):
        result = parse_message("Ranchi, dhan")
        assert isinstance(result, ParsedMessage)
        assert result.village == "Ranchi"
        assert result.crop == "rice"

    def test_extra_spaces(self):
        result = parse_message("  Chitrakoot ,  gehun  ")
        assert isinstance(result, ParsedMessage)
        assert result.village == "Chitrakoot"
        assert result.crop == "wheat"

    def test_reverse_order(self):
        """Crop first, village second."""
        result = parse_message("wheat, Rampur")
        assert isinstance(result, ParsedMessage)
        assert result.village == "Rampur"
        assert result.crop == "wheat"


class TestSpaceSeparated:
    """No comma — space-separated."""

    def test_basic(self):
        result = parse_message("Chitrakoot gehun")
        assert isinstance(result, ParsedMessage)
        assert result.village == "Chitrakoot"
        assert result.crop == "wheat"

    def test_english(self):
        result = parse_message("Rampur wheat")
        assert isinstance(result, ParsedMessage)
        assert result.village == "Rampur"
        assert result.crop == "wheat"


class TestMultiWordVillage:
    """Village names with spaces."""

    def test_two_words_comma(self):
        result = parse_message("Ram Garh, dhan")
        assert isinstance(result, ParsedMessage)
        assert result.village == "Ram Garh"
        assert result.crop == "rice"


class TestFuzzyMatching:
    """Misspelled crop names."""

    def test_misspelled_gehu(self):
        result = parse_message("Rampur, gehu")
        assert isinstance(result, ParsedMessage)
        assert result.crop == "wheat"

    def test_misspelled_dhaan(self):
        result = parse_message("Ranchi, dhaan")
        assert isinstance(result, ParsedMessage)
        assert result.crop == "rice"


class TestDevanagari:
    """Devanagari script input."""

    def test_devanagari_crop(self):
        result = parse_message("Chitrakoot, गेहूं")
        assert isinstance(result, ParsedMessage)
        assert result.crop == "wheat"


class TestEdgeCases:
    """Error cases and boundary inputs."""

    def test_empty_string(self):
        result = parse_message("")
        assert isinstance(result, ParseError)
        assert result.reason == "empty"

    def test_whitespace_only(self):
        result = parse_message("   ")
        assert isinstance(result, ParseError)
        assert result.reason == "empty"

    def test_gibberish(self):
        result = parse_message("asdfghjkl")
        assert isinstance(result, ParseError)

    def test_single_crop_word(self):
        """Single crop name without village."""
        result = parse_message("gehun")
        assert isinstance(result, ParseError)
        assert result.reason == "no_village"

    def test_single_unknown_word(self):
        result = parse_message("xyz")
        assert isinstance(result, ParseError)


class TestCropData:
    """Test crop resolution directly."""

    def test_all_major_crops(self):
        from app.utils.crop_data import resolve_crop_name

        assert resolve_crop_name("gehun") == "wheat"
        assert resolve_crop_name("dhan") == "rice"
        assert resolve_crop_name("makka") == "maize"
        assert resolve_crop_name("chana") == "chickpea"
        assert resolve_crop_name("kapas") == "cotton"
        assert resolve_crop_name("sarson") == "mustard"
        assert resolve_crop_name("arhar") == "pigeon_pea"
        assert resolve_crop_name("soybean") == "soybean"

    def test_english_names(self):
        from app.utils.crop_data import resolve_crop_name

        assert resolve_crop_name("wheat") == "wheat"
        assert resolve_crop_name("rice") == "rice"
        assert resolve_crop_name("cotton") == "cotton"

    def test_unknown_crop(self):
        from app.utils.crop_data import resolve_crop_name

        assert resolve_crop_name("computer") is None
        assert resolve_crop_name("xyz") is None
        assert resolve_crop_name("hello") is None
