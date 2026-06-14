from __future__ import annotations

"""
JalSense 2.0 — WhatsApp Cloud API Integration

Handles sending messages (text + voice notes) to farmers via Meta's
WhatsApp Cloud API. Also extracts incoming message data from webhook payloads.

Two-step voice note sending:
1. Upload audio file to Meta's media server → get media_id
2. Send audio message referencing media_id

Auto-deletes audio files after upload (prevents disk fill).
"""

import os
import logging
from dataclasses import dataclass

import httpx

from app.config import get_settings
from app.utils import mask_phone

logger = logging.getLogger(__name__)
settings = get_settings()

META_API_BASE = f"https://graph.facebook.com/v21.0/{settings.whatsapp_phone_number_id}"


@dataclass
class IncomingMessage:
    """Parsed incoming WhatsApp message."""
    message_id: str        # Unique message ID from Meta (for dedup)
    phone: str             # Sender's phone number (e.g., "917679144006")
    text: str              # Message body text
    sender_name: str       # WhatsApp profile name (may be empty)
    timestamp: str         # Unix timestamp string


def extract_message(webhook_data: dict) -> IncomingMessage | None:
    """
    Extract message details from a Meta webhook payload.
    Returns None if the payload doesn't contain a text message
    (e.g., status updates, read receipts, media messages).
    """
    try:
        entry = webhook_data.get("entry", [])
        if not entry:
            return None

        changes = entry[0].get("changes", [])
        if not changes:
            return None

        value = changes[0].get("value", {})
        messages = value.get("messages", [])
        if not messages:
            return None

        msg = messages[0]

        # Only handle text messages
        if msg.get("type") != "text":
            logger.info(f"Non-text message type: {msg.get('type')}, skipping")
            return None

        # Extract sender info
        contacts = value.get("contacts", [{}])
        sender_name = ""
        if contacts:
            sender_name = contacts[0].get("profile", {}).get("name", "")

        return IncomingMessage(
            message_id=msg.get("id", ""),
            phone=msg.get("from", ""),
            text=msg.get("text", {}).get("body", ""),
            sender_name=sender_name,
            timestamp=msg.get("timestamp", ""),
        )

    except (KeyError, IndexError, TypeError) as e:
        logger.warning(f"Failed to parse webhook payload: {e}")
        return None


async def send_text_message(phone: str, text: str) -> bool:
    """
    Send a plain text message to a WhatsApp number.
    Used as fallback when voice note generation fails.

    Returns True on success, False on failure.
    """
    if not settings.whatsapp_access_token:
        logger.warning("WhatsApp access token not configured, skipping send")
        return False

    url = f"{META_API_BASE}/messages"
    headers = {
        "Authorization": f"Bearer {settings.whatsapp_access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": text},
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            logger.info(f"Text message sent to {mask_phone(phone)}")
            return True
    except httpx.HTTPStatusError as e:
        logger.error(f"WhatsApp text send failed ({e.response.status_code}): {e.response.text}")
    except Exception as e:
        logger.error(f"WhatsApp text send error: {e}")

    return False


async def _upload_media(audio_path: str) -> str | None:
    """
    Upload an audio file to Meta's media server.
    Returns the media_id on success, None on failure.
    """
    url = f"{META_API_BASE}/media"
    headers = {
        "Authorization": f"Bearer {settings.whatsapp_access_token}",
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            with open(audio_path, "rb") as f:
                files = {
                    "file": (os.path.basename(audio_path), f, "audio/ogg"),
                }
                data = {
                    "messaging_product": "whatsapp",
                    "type": "audio/ogg",
                }
                response = await client.post(url, headers=headers, files=files, data=data)
                response.raise_for_status()

        media_id = response.json().get("id")
        logger.info(f"Media uploaded: {media_id}")
        return media_id

    except Exception as e:
        logger.error(f"Media upload failed: {e}")
        return None


async def send_voice_note(phone: str, audio_path: str) -> bool:
    """
    Upload audio file and send as WhatsApp voice note.
    Auto-deletes the audio file after upload (success or failure).

    Returns True on success, False on failure.
    """
    if not settings.whatsapp_access_token:
        logger.warning("WhatsApp access token not configured, skipping send")
        _cleanup_file(audio_path)
        return False

    try:
        # Step 1: Upload audio to Meta
        media_id = await _upload_media(audio_path)
        if not media_id:
            _cleanup_file(audio_path)
            return False

        # Step 2: Send audio message
        url = f"{META_API_BASE}/messages"
        headers = {
            "Authorization": f"Bearer {settings.whatsapp_access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "audio",
            "audio": {"id": media_id},
        }

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()

        logger.info(f"Voice note sent to {mask_phone(phone)}")

        # Step 3: Clean up local file
        _cleanup_file(audio_path)
        return True

    except httpx.HTTPStatusError as e:
        logger.error(f"WhatsApp voice send failed ({e.response.status_code}): {e.response.text}")
    except Exception as e:
        logger.error(f"WhatsApp voice send error: {e}")

    # Always clean up, even on failure
    _cleanup_file(audio_path)
    return False


def _cleanup_file(path: str) -> None:
    """Safely delete a file, ignoring errors."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
            logger.debug(f"Cleaned up file: {path}")
    except OSError as e:
        logger.warning(f"Failed to clean up {path}: {e}")
