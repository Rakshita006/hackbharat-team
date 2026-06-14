from __future__ import annotations

# Webhook router for WhatsApp events.
#
# GET  /webhook -> Handshake verification with Meta.
# POST /webhook -> Process incoming messages asynchronously.
#
# Note: We return 200 OK immediately and offload pipeline logic to BackgroundTasks
# to prevent Meta from retrying requests due to timeouts.

import json
import logging

from fastapi import APIRouter, Request, BackgroundTasks, Response, HTTPException, Query

from app.config import get_settings
from app.utils.dedup import message_dedup
from app.utils.security import verify_signature, mask_phone
from app.utils.logging_utils import correlation_id_ctx
from app.services.whatsapp import extract_message, send_text_message, IncomingMessage
from app.services.pipeline import run_full_pipeline, NON_TEXT_HINDI

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(tags=["WhatsApp"])


@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    # Meta webhook verification handshake.
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        logger.info("Webhook verified successfully")
        return Response(content=hub_challenge, media_type="text/plain")

    logger.warning(f"Webhook verification failed: mode={hub_mode}")
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()

    # Validate HMAC signature if app secret is configured
    if settings.whatsapp_app_secret:
        signature = request.headers.get("X-Hub-Signature-256")
        if not signature:
            logger.warning("Missing X-Hub-Signature-256 header")
            raise HTTPException(status_code=401, detail="Missing signature header")

        if not verify_signature(body, signature, settings.whatsapp_app_secret):
            logger.warning("Invalid webhook signature")
            raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        data = json.loads(body)
    except Exception:
        return Response(status_code=200)

    # Extract text message payload
    message = extract_message(data)
    if not message:
        return Response(status_code=200)

    # Skip duplicates from Meta retry storms
    if message_dedup.is_duplicate(message.message_id):
        logger.info(f"Duplicate message {message.message_id}, skipping")
        return Response(status_code=200)

    logger.info(
        f"New message from {mask_phone(message.phone)}: '{message.text}' "
        f"(id={message.message_id})"
    )

    # Propagate logging correlation ID context to async worker task
    corr_id = correlation_id_ctx.get()
    background_tasks.add_task(_process_message_with_context, message, corr_id)

    return Response(status_code=200)


async def _process_message_with_context(message: IncomingMessage, correlation_id: str):
    # Context wrapper for background worker logs
    token = correlation_id_ctx.set(correlation_id)
    try:
        await _process_message(message)
    finally:
        correlation_id_ctx.reset(token)


async def _process_message(message: IncomingMessage):
    # Background pipeline worker. Errors are caught internally to prevent thread crashes.
    try:
        result = await run_full_pipeline(message)
        if result.error:
            logger.warning(f"Pipeline completed with error: {result.error}")
    except Exception as e:
        logger.error(f"Pipeline crashed for {mask_phone(message.phone)}: {e}", exc_info=True)
        # Fall back to text apology if something crashes
        try:
            await send_text_message(
                message.phone,
                "Maaf kijiye, abhi kuch technical samasya hai. Kripya thodi der baad phir koshish karein."
            )
        except Exception:
            pass
