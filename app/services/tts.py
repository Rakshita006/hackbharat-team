from __future__ import annotations

"""
JalSense 2.0 — Hindi Text-to-Speech (edge-tts)

Generates Hindi voice notes using Microsoft Edge's TTS service.
edge-tts is async-native, reliable on server IPs (unlike gTTS),
and produces clear Hindi speech with the SwaraNeural voice.

Output: OGG/Opus file (WhatsApp voice note format with waveform UI).
Fallback: returns None → caller sends text message instead.
"""

import os
import uuid
import logging
import asyncio
import tempfile

import edge_tts
from pydub import AudioSegment

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


async def generate_voice_note(text_hindi: str) -> str | None:
    """
    Generate a Hindi voice note from text.

    Flow: Text → edge-tts (MP3) → pydub (OGG/Opus)

    Returns:
        Path to the .ogg file on success, None on failure.
        Caller is responsible for deleting the file after use.
    """
    if not text_hindi or not text_hindi.strip():
        logger.warning("Empty text passed to TTS, skipping")
        return None

    # Generate unique filenames in temp directory
    file_id = uuid.uuid4().hex[:8]
    tmp_dir = tempfile.gettempdir()
    mp3_path = os.path.join(tmp_dir, f"jalsense_{file_id}.mp3")
    ogg_path = os.path.join(tmp_dir, f"jalsense_{file_id}.ogg")

    try:
        # Step 1: Generate MP3 via edge-tts (async)
        communicate = edge_tts.Communicate(
            text_hindi,
            voice=settings.tts_voice,  # hi-IN-SwaraNeural
            rate="-5%",                # Slightly slower for clarity
        )
        await asyncio.wait_for(
            communicate.save(mp3_path),
            timeout=settings.tts_timeout,
        )

        if not os.path.exists(mp3_path) or os.path.getsize(mp3_path) == 0:
            logger.error("edge-tts produced empty MP3 file")
            return None

        # Step 2: Convert MP3 → OGG/Opus (sync, but fast ~100ms)
        audio = AudioSegment.from_mp3(mp3_path)
        audio.export(ogg_path, format="ogg", codec="libopus")

        # Step 3: Clean up intermediate MP3
        os.remove(mp3_path)

        file_size = os.path.getsize(ogg_path)
        logger.info(
            f"Voice note generated: {ogg_path} ({file_size / 1024:.1f} KB, "
            f"{len(audio) / 1000:.1f}s duration)"
        )

        return ogg_path

    except asyncio.TimeoutError:
        logger.warning(f"TTS timeout ({settings.tts_timeout}s)")
    except FileNotFoundError as e:
        if "ffmpeg" in str(e).lower() or "ffprobe" in str(e).lower():
            logger.error(
                "ffmpeg not found! Install it:\n"
                "  Windows: choco install ffmpeg\n"
                "  Ubuntu:  sudo apt install ffmpeg\n"
                "  Mac:     brew install ffmpeg"
            )
        else:
            logger.error(f"File not found during TTS: {e}")
    except Exception as e:
        logger.error(f"TTS generation failed: {e}")

    # Cleanup on failure
    for path in [mp3_path, ogg_path]:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass

    return None
