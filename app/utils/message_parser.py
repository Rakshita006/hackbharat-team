from __future__ import annotations

"""
JalSense 2.0 — Message Parser

Parses freeform Hindi/English/mixed farmer messages into structured
(village, crop) pairs. Handles:
- Comma-separated: "Chitrakoot, gehun"
- Space-separated: "chitrakoot gehun"
- Multi-word villages: "Ram Garh, dhan"
- Devanagari: "चित्रकूट, गेहूं"
- Mixed case: "CHITRAKOOT WHEAT"
- Misspellings: "rampur, gehu" (via fuzzy matching in crop_data)

The parser is deliberately simple (no ML/LLM) for hackathon reliability.
Every unparsed message is logged for post-demo analysis.
"""

import re
import unicodedata
import logging
from dataclasses import dataclass

from app.utils.crop_data import resolve_crop_name, CROP_MAP

logger = logging.getLogger(__name__)


@dataclass
class ParsedMessage:
    """Successfully parsed farmer message."""
    village: str
    crop: str  # Internal English name: "wheat", "rice", etc.


@dataclass
class ParseError:
    """Failed to parse — includes reason for logging/response."""
    reason: str  # "no_village", "no_crop", "empty", "gibberish"
    raw_message: str


def parse_message(raw_text: str) -> ParsedMessage | ParseError:
    """
    Parse a farmer's WhatsApp message into (village, crop).

    Returns ParsedMessage on success, ParseError on failure.
    """
    if not raw_text or not raw_text.strip():
        return ParseError(reason="empty", raw_message=raw_text or "")

    # Step 1: Normalize Unicode (NFC) and strip
    text = unicodedata.normalize("NFC", raw_text).strip()

    # Step 2: Try comma-separated format first (most reliable)
    if "," in text:
        result = _parse_comma_separated(text)
        if result:
            return result

    # Step 3: Try space-separated format
    result = _parse_space_separated(text)
    if result:
        return result

    # Step 4: Nothing worked
    logger.warning(f"Failed to parse message: '{raw_text}'")
    return ParseError(reason="gibberish", raw_message=raw_text)


def _parse_comma_separated(text: str) -> ParsedMessage | None:
    """
    Parse "Village, Crop" or "Crop, Village" format.
    We don't assume order — we check which part matches a crop name.
    """
    parts = [p.strip() for p in text.split(",", maxsplit=1)]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None

    # Try part[1] as crop first (most common: "Village, Crop")
    crop = resolve_crop_name(parts[1])
    if crop:
        village = _clean_village_name(parts[0])
        if village:
            return ParsedMessage(village=village, crop=crop)

    # Try part[0] as crop (reverse order: "Crop, Village")
    crop = resolve_crop_name(parts[0])
    if crop:
        village = _clean_village_name(parts[1])
        if village:
            return ParsedMessage(village=village, crop=crop)

    return None


def _parse_space_separated(text: str) -> ParsedMessage | None:
    """
    Parse "village crop" format without comma.
    Strategy: try each word as a potential crop name.
    Start from the end (crop is usually the last word).
    Remaining words form the village name.
    """
    text_lower = text.lower()
    words = text_lower.split()

    if len(words) < 2:
        # Single word — can't determine both village and crop
        # Check if it's just a crop name or just a village
        crop = resolve_crop_name(words[0]) if words else None
        if crop:
            return ParseError(reason="no_village", raw_message=text)
        return ParseError(reason="no_crop", raw_message=text)

    # Try last word as crop
    crop = resolve_crop_name(words[-1])
    if crop:
        village_words = words[:-1]
        # Filter out common filler words
        village_words = _remove_fillers(village_words)
        if village_words:
            village = _clean_village_name(" ".join(village_words))
            if village:
                return ParsedMessage(village=village, crop=crop)

    # Try second-to-last word as crop (handles "Rampur gehun hai" → crop=gehun)
    if len(words) >= 3:
        crop = resolve_crop_name(words[-2])
        if crop:
            village_words = words[:-2]
            village_words = _remove_fillers(village_words)
            if village_words:
                village = _clean_village_name(" ".join(village_words))
                if village:
                    return ParsedMessage(village=village, crop=crop)

    # Try first word as crop (reverse order: "gehun Rampur")
    crop = resolve_crop_name(words[0])
    if crop:
        village_words = words[1:]
        village_words = _remove_fillers(village_words)
        if village_words:
            village = _clean_village_name(" ".join(village_words))
            if village:
                return ParsedMessage(village=village, crop=crop)

    return None


# Common Hindi filler words to strip from village names
_FILLER_WORDS = {
    "mein", "me", "mai", "ka", "ki", "ke", "hai", "hain", "se",
    "mera", "meri", "gaon", "gaav", "village", "bhai", "ji",
    "haan", "ha", "yes", "में", "का", "की", "के", "है",
    "मेरा", "मेरी", "गांव", "गाँव",
}


def _remove_fillers(words: list[str]) -> list[str]:
    """Remove common filler words that aren't part of the village name."""
    filtered = [w for w in words if w.lower() not in _FILLER_WORDS]
    # If filtering removed everything, return original (the 'filler' was the village name)
    return filtered if filtered else words


def _clean_village_name(raw: str) -> str | None:
    """
    Clean up a village name:
    - Title case
    - Remove extra spaces
    - Reject if too short or obviously not a name
    """
    cleaned = re.sub(r"\s+", " ", raw).strip()
    if len(cleaned) < 2:
        return None

    # Title-case for romanized text, preserve Devanagari as-is
    if all(ord(c) < 0x900 or c == " " for c in cleaned):
        cleaned = cleaned.title()

    return cleaned
