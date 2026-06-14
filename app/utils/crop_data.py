from __future__ import annotations

"""
JalSense 2.0 — Crop Data

Hindi ↔ English crop name mapping with fuzzy matching support.
Covers romanized Hindi, Devanagari script, and English names.

Fuzzy matching (Levenshtein distance ≤ 2) handles common misspellings
like "gehu" → "gehun" → "wheat".
"""

# ── Exact crop name mapping ──
# Keys: all known spellings/scripts. Values: internal English name.
CROP_MAP: dict[str, str] = {
    # Wheat
    "gehun": "wheat",
    "gehu": "wheat",
    "gehunn": "wheat",
    "गेहूं": "wheat",
    "गेहू": "wheat",
    "wheat": "wheat",

    # Rice
    "dhan": "rice",
    "dhaan": "rice",
    "chawal": "rice",
    "chaval": "rice",
    "धान": "rice",
    "चावल": "rice",
    "rice": "rice",
    "paddy": "rice",

    # Maize
    "makka": "maize",
    "makki": "maize",
    "मक्का": "maize",
    "maize": "maize",
    "corn": "maize",

    # Chickpea
    "chana": "chickpea",
    "channa": "chickpea",
    "चना": "chickpea",
    "chickpea": "chickpea",
    "gram": "chickpea",

    # Soybean
    "soybean": "soybean",
    "soyabean": "soybean",
    "soya": "soybean",
    "सोयाबीन": "soybean",

    # Cotton
    "kapas": "cotton",
    "kapaas": "cotton",
    "कपास": "cotton",
    "cotton": "cotton",

    # Mustard
    "sarson": "mustard",
    "sarso": "mustard",
    "सरसों": "mustard",
    "mustard": "mustard",

    # Pigeon Pea (Arhar/Toor)
    "arhar": "pigeon_pea",
    "arhr": "pigeon_pea",
    "toor": "pigeon_pea",
    "tur": "pigeon_pea",
    "अरहर": "pigeon_pea",
    "pigeon_pea": "pigeon_pea",

    # Sugarcane
    "ganna": "sugarcane",
    "गन्ना": "sugarcane",
    "sugarcane": "sugarcane",
}

# Set of all valid internal crop names
VALID_CROPS = set(CROP_MAP.values())

# Display names for user-facing output
CROP_DISPLAY_NAMES: dict[str, str] = {
    "wheat": "Gehun (Wheat)",
    "rice": "Dhan (Rice)",
    "maize": "Makka (Maize)",
    "chickpea": "Chana (Chickpea)",
    "soybean": "Soybean",
    "cotton": "Kapas (Cotton)",
    "mustard": "Sarson (Mustard)",
    "pigeon_pea": "Arhar (Pigeon Pea)",
    "sugarcane": "Ganna (Sugarcane)",
}


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)

    prev_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            # Cost is 0 if characters match, 1 otherwise
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row

    return prev_row[-1]


def resolve_crop_name(raw: str) -> str | None:
    """
    Resolve a raw crop input (Hindi/English/misspelled) to an internal
    English crop name.

    Strategy:
    1. Exact match in CROP_MAP (case-insensitive)
    2. Fuzzy match with Levenshtein distance ≤ 2
    3. Return None if no match found

    Args:
        raw: The raw crop name from the farmer's message

    Returns:
        Internal crop name (e.g., "wheat") or None
    """
    cleaned = raw.strip().lower()

    # 1. Exact match
    if cleaned in CROP_MAP:
        return CROP_MAP[cleaned]

    # 2. Fuzzy match — find closest known crop name within distance 2
    # Only attempt fuzzy matching for inputs with 4+ characters
    # (short words produce too many false positives, e.g., 'banana' → 'ganna')
    if len(cleaned) < 4:
        return None

    best_match = None
    best_distance = 3  # Only accept distance ≤ 2

    for known_name, internal_name in CROP_MAP.items():
        # Skip Devanagari entries for fuzzy matching (they won't fuzzy-match romanized input)
        if any(ord(c) > 0x900 for c in known_name):
            continue
        # Skip very short dictionary entries to avoid false matches
        if len(known_name) < 4:
            continue

        dist = _levenshtein_distance(cleaned, known_name)
        if dist < best_distance:
            best_distance = dist
            best_match = internal_name

    return best_match


def get_crop_kc(crop: str, age_days: int) -> float:
    # Crop growth parameters from FAO-56:
    # (stage1_len, stage2_len, stage3_len, stage4_len, kc_ini, kc_mid, kc_end)
    crop_params = {
        "wheat": (20, 30, 40, 30, 0.3, 1.15, 0.4),
        "rice": (20, 30, 40, 30, 1.05, 1.20, 0.6),
        "maize": (20, 30, 30, 30, 0.3, 1.15, 0.5),
        "cotton": (30, 50, 40, 30, 0.35, 1.20, 0.6),
        "mustard": (20, 30, 40, 20, 0.3, 1.10, 0.35),
        "chickpea": (20, 25, 35, 30, 0.4, 1.00, 0.35),
        "soybean": (20, 30, 40, 20, 0.4, 1.15, 0.5),
        "pigeon_pea": (20, 40, 60, 30, 0.3, 1.05, 0.4),
        "sugarcane": (30, 60, 180, 60, 0.4, 1.25, 0.75),
    }

    params = crop_params.get(crop, (20, 30, 40, 30, 0.3, 1.1, 0.4))
    len1, len2, len3, len4, kc_ini, kc_mid, kc_end = params

    if age_days <= len1:
        return kc_ini
    elif age_days <= (len1 + len2):
        # Linear interpolation from initial to mid coefficient
        fraction = (age_days - len1) / len2
        return round(kc_ini + fraction * (kc_mid - kc_ini), 2)
    elif age_days <= (len1 + len2 + len3):
        return kc_mid
    elif age_days <= (len1 + len2 + len3 + len4):
        # Linear interpolation from mid to end coefficient
        fraction = (age_days - (len1 + len2 + len3)) / len4
        return round(kc_mid - fraction * (kc_mid - kc_end), 2)
    else:
        return kc_end

