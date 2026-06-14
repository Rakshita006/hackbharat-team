from __future__ import annotations

"""
JalSense 2.0 — Security Utilities Unit Tests

Verifies PII masking and HMAC signature verification logic.
"""

import hmac
import hashlib
from app.utils.security import mask_phone, verify_signature


def test_mask_phone():
    assert mask_phone("919876543210") == "********3210"
    assert mask_phone("1234") == "****"
    assert mask_phone("") == ""
    assert mask_phone(None) == ""
    assert mask_phone("1") == "****"
    assert mask_phone("12345") == "*2345"


def test_verify_signature():
    secret = "test_secret"
    payload = b"test_payload"
    
    # Compute expected signature
    expected_hash = hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    # Verify exact matching cases (with and without prefix)
    assert verify_signature(payload, f"sha256={expected_hash}", secret) is True
    assert verify_signature(payload, expected_hash, secret) is True
    
    # Verify mismatch cases
    assert verify_signature(payload, "invalid_sig", secret) is False
    assert verify_signature(payload, f"sha256={expected_hash}", "wrong_secret") is False
    assert verify_signature(b"wrong_payload", f"sha256={expected_hash}", secret) is False
    assert verify_signature(payload, "", secret) is False
    assert verify_signature(payload, None, secret) is False
