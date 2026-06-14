# Helper utilities for request validation and PII log masking.

import hmac
import hashlib


def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    # Verifies incoming webhook signature against Meta App Secret
    if not signature:
        return False

    # Strip standard 'sha256=' prefix if present
    if signature.startswith("sha256="):
        sig_hash = signature[7:]
    else:
        sig_hash = signature

    try:
        expected = hmac.new(
            secret.encode("utf-8"),
            payload,
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(sig_hash, expected)
    except Exception:
        return False


def mask_phone(phone: str) -> str:
    # Redacts phone numbers to keep logs PII-clean (e.g. ********1234)
    if not phone:
        return ""
    phone_str = str(phone).strip()
    if len(phone_str) <= 4:
        return "****"
    return "*" * (len(phone_str) - 4) + phone_str[-4:]
