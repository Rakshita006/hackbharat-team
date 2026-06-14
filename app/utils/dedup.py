"""
JalSense 2.0 — Message Deduplication

Prevents duplicate processing when Meta retries webhook delivery.
Meta retries failed webhooks up to 7 times — without dedup, the farmer
would receive 7 identical voice notes.

Uses an in-memory dict of message IDs with automatic expiry.
A 5-minute TTL window is sufficient since Meta's retry window is ~5 minutes.
"""

import time
import threading


class MessageDedup:
    """Track recently processed WhatsApp message IDs to prevent duplicates."""

    def __init__(self, ttl: int = 300):
        """
        Args:
            ttl: How long to remember a message ID (seconds). Default 5 minutes.
        """
        self._seen: dict[str, float] = {}
        self._ttl = ttl
        self._lock = threading.Lock()

    def is_duplicate(self, message_id: str) -> bool:
        """
        Check if this message was already processed.
        If not seen before, marks it as seen and returns False.
        If seen within TTL window, returns True.
        """
        with self._lock:
            self._cleanup()
            if message_id in self._seen:
                return True
            self._seen[message_id] = time.time()
            return False

    def _cleanup(self) -> None:
        """Remove expired entries. Called internally under lock."""
        now = time.time()
        expired = [k for k, v in self._seen.items() if now - v >= self._ttl]
        for k in expired:
            del self._seen[k]

    def __len__(self) -> int:
        return len(self._seen)


# ── Global singleton ──
message_dedup = MessageDedup(ttl=300)
