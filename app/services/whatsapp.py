from __future__ import annotations

"""
JalSense 2.0 — WhatsApp Twilio Integration

Handles sending messages (text + voice notes) to farmers via Twilio's
WhatsApp Sandbox API. Also extracts incoming message data from webhook payloads.

Two-step voice note sending:
1. Upload audio file to a public URL (via file hosting)
2. Send audio message referencing the URL

Auto-deletes audio files after sending.
"""

import os
import logging
from dataclasses import dataclass

from twilio.rest import Client
from twilio.request_validator import RequestValidator

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class IncomingMessage:
    """Parsed incoming WhatsApp message."""
    message_id: str        # Unique message ID from Twilio
    phone: str             # Sender's phone number (e.g., "whatsapp:+917679144006")
    text: str              # Message body text
    sender_name: str       # WhatsApp profile name (may be empty)
    timestamp: str         # Timestamp string


def get_twilio_client() -> Client:
    """Get authenticated Twilio client."""
    return Client(settings.twilio_account_sid, settings.twilio_auth_token)


def extract_message(webhook_data: dict) -> IncomingMessage | None:
    """
    Extract message details from a Twilio webhook payload.
    Twilio sends form data, FastAPI parses it as a dict.
    Returns None if the payload doesn't contain a text message.
    """
    try:
        message_sid = webhook_data.get("MessageSid", "")
        phone = webhook_data.get("From", "")
        text = webhook_data.get("Body", "").strip()
        sender_name = webhook_data.get("ProfileName", "")
        timestamp = webhook_data.get("Timestamp", "")
        num_media = int(webhook_data.get("NumMedia", 0))

        # Skip media messages
        if num_media > 0:
            logger.info("Media message received, skipping")
            return None

        if not phone or not text:
            return None

        return IncomingMessage(
            message_id=message_sid,
            phone=phone,
            text=text,
            sender_name=sender_name,
            timestamp=timestamp,
        )

    except Exception as e:
        logger.warning(f"Failed to parse Twilio webhook payload: {e}")
        return None


async def send_text_message(phone: str, text: str) -> bool:
    """
    Send a plain text message to a WhatsApp number via Twilio.
    Used as fallback when voice note generation fails.

    Returns True on success, False on failure.
    """
    if not settings.twilio_account_sid:
        logger.warning("Twilio credentials not configured, skipping send")
        return False

    try:
        client = get_twilio_client()
        message = client.messages.create(
            from_=settings.twilio_whatsapp_number,
            to=phone,
            body=text,
        )
        logger.info(f"Text message sent to {phone[-4:]} (SID: {message.sid})")
        return True
    except Exception as e:
        logger.error(f"Twilio text send error: {e}")
        return False


async def send_voice_note(phone: str, audio_path: str) -> bool:
    """
    Send a voice note to a WhatsApp number via Twilio.
    Twilio requires a publicly accessible URL for media.
    For hackathon: we send text fallback with the alert message.

    Returns True on success, False on failure.
    """
    if not settings.twilio_account_sid:
        logger.warning("Twilio credentials not configured, skipping send")
        _cleanup_file(audio_path)
        return False

    try:
        # For hackathon demo: read the audio file exists confirmation
        # then send as text (Twilio sandbox has media URL restrictions)
        # In production: upload to S3/Cloudinary and send media URL
        file_exists = os.path.exists(audio_path)
        file_size = os.path.getsize(audio_path) if file_exists else 0
        logger.info(f"Voice note ready: {audio_path} ({file_size/1024:.1f} KB)")

        # Check if we have a public URL configured for media hosting
        media_url = os.environ.get("MEDIA_BASE_URL", "")

        if media_url:
            # Production path: send actual voice note
            client = get_twilio_client()
            filename = os.path.basename(audio_path)
            full_media_url = f"{media_url}/{filename}"
            message = client.messages.create(
                from_=settings.twilio_whatsapp_number,
                to=phone,
                media_url=[full_media_url],
            )
            logger.info(f"Voice note sent to {phone[-4:]} (SID: {message.sid})")
        else:
            # Sandbox path: voice notes need public URLs which sandbox restricts
            # Send text message instead — still delivers the alert
            logger.info("No MEDIA_BASE_URL set, sending text fallback")
            _cleanup_file(audio_path)
            return False

        _cleanup_file(audio_path)
        return True

    except Exception as e:
        logger.error(f"Twilio voice send error: {e}")
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