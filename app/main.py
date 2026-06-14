# Main FastAPI app entry point.
# Run with: uvicorn app.main:app --reload

import os
import shutil
import logging
import sys
from contextlib import asynccontextmanager

# Dynamically add winget's ffmpeg to PATH if uvicorn inherited an outdated shell environment.
user_profile = os.environ.get("USERPROFILE")
if user_profile:
    winget_links = os.path.join(user_profile, "AppData", "Local", "Microsoft", "WinGet", "Links")
    winget_packages_bin = os.path.join(
        user_profile,
        "AppData",
        "Local",
        "Microsoft",
        "WinGet",
        "Packages",
        "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe",
        "ffmpeg-8.1.1-full_build",
        "bin",
    )
    for p in (winget_links, winget_packages_bin):
        if os.path.exists(p) and p not in os.environ["PATH"]:
            os.environ["PATH"] = p + os.path.pathsep + os.environ["PATH"]

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import init_database
from app.services.satellite import init_gee, load_demo_cache, gee_is_connected
from app.routers import webhook_router, dashboard_router, demo_router
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from app.utils import limiter

from app.utils.logging_utils import configure_logging, CorrelationIDMiddleware

configure_logging()
logger = logging.getLogger("jalsense")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Verify critical integrations on startup so we catch setup issues early
    logger.info("=" * 60)
    logger.info("  JalSense 2.0 Backend — Starting Up")
    logger.info("=" * 60)

    # 1. Check FFmpeg (needed for OGG audio notes)
    if shutil.which("ffmpeg"):
        logger.info("✅ ffmpeg found")
    else:
        logger.error(
            "❌ ffmpeg NOT found! Voice notes will fail.\n"
            "   Install it:\n"
            "     Windows: winget install Gyan.FFmpeg\n"
            "     Ubuntu:  sudo apt install ffmpeg\n"
            "     Mac:     brew install ffmpeg"
        )

    # 2. Setup SQLite database
    try:
        init_database()
        logger.info("✅ Database initialized")
    except Exception as e:
        logger.error(f"❌ Database init failed: {e}")
        raise

    # 3. Connect to Google Earth Engine
    gee_ok = init_gee()
    if gee_ok:
        logger.info("✅ Google Earth Engine connected")
    else:
        logger.warning("⚠️  GEE not connected — using cached/demo data only")

    # 4. Load precomputed demo cache
    demo_count = load_demo_cache()
    if demo_count > 0:
        logger.info(f"✅ Demo cache loaded ({demo_count} villages)")
    else:
        logger.warning("⚠️  No demo cache loaded — GEE will be called for every request")

    # 5. Check WhatsApp API configs
    if settings.whatsapp_access_token:
        logger.info("✅ WhatsApp access token configured")
    else:
        logger.warning("⚠️  WhatsApp not configured — messages won't be sent")

    logger.info("=" * 60)
    logger.info("  🚀 JalSense 2.0 Backend — Ready!")
    logger.info("  📖 API docs: http://localhost:8000/docs")
    logger.info("  🏥 Health:   http://localhost:8000/health")
    logger.info("=" * 60)

    yield
    logger.info("JalSense 2.0 shutting down")


app = FastAPI(
    title="JalSense 2.0 API",
    description="Water stress prediction backend for Indian farmers. Sentinel-2 + Weather forecasts -> WhatsApp Hindi voice alerts.",
    version="2.0.0",
    lifespan=lifespan,
)

# Enable rate limiting and exception handlers
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Request tracking middleware
app.add_middleware(CorrelationIDMiddleware)

# Enable CORS for dashboard UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Mount all endpoint routers
app.include_router(webhook_router)
app.include_router(dashboard_router)
app.include_router(demo_router)


@app.get("/health", tags=["System"])
async def health():
    # Simple check endpoint to verify backend service status before demo runs
    ffmpeg_ok = shutil.which("ffmpeg") is not None
    return {
        "status": "healthy" if ffmpeg_ok else "degraded",
        "checks": {
            "gee_connected": gee_is_connected(),
            "ffmpeg_available": ffmpeg_ok,
            "whatsapp_configured": bool(settings.whatsapp_access_token),
        },
        "version": "2.0.0",
    }
