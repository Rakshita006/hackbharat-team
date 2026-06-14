from __future__ import annotations

"""
JalSense 2.0 — Dashboard REST API

Endpoints for the web dashboard, all protected with API key.
Phone numbers are NEVER exposed — privacy by design.

Endpoints:
- GET  /api/farmers      → List all farmers (for map + feed)
- GET  /api/farmers/{id} → Single farmer + alert history
- GET  /api/stats        → Aggregate statistics
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.config import get_settings
from app.database import get_db
from app.models.farmer import Farmer
from app.models.alert import Alert

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api", tags=["Dashboard"])


# ── API Key Authentication ──

async def verify_api_key(x_api_key: str = Header(..., alias="X-API-Key")):
    """Verify the dashboard API key. Simple but effective for hackathon."""
    if x_api_key != settings.dashboard_api_key:
        raise HTTPException(status_code=403, detail="Invalid API key")


# ── Endpoints ──

@router.get("/farmers", dependencies=[Depends(verify_api_key)])
async def list_farmers(db: Session = Depends(get_db)):
    """
    List all registered farmers with their current stress levels.
    Used by the dashboard map (green/yellow/red dots) and farmer feed.

    NOTE: Phone numbers are NOT included — privacy by design.
    """
    farmers = db.query(Farmer).order_by(Farmer.registered_at.desc()).all()

    return {
        "count": len(farmers),
        "farmers": [
            {
                "id": f.id,
                "farmer_name": f.farmer_name or "Unknown",
                "village_name": f.village_name,
                "district": f.district,
                "state": f.state,
                "crop": f.crop_name,
                "latitude": f.latitude,
                "longitude": f.longitude,
                "stress_level": f.current_stress_level,
                "last_alert_at": f.last_alert_sent_at.isoformat() if f.last_alert_sent_at else None,
                "registered_at": f.registered_at.isoformat() if f.registered_at else None,
            }
            for f in farmers
        ],
    }


@router.get("/farmers/{farmer_id}", dependencies=[Depends(verify_api_key)])
async def get_farmer(farmer_id: int, db: Session = Depends(get_db)):
    """
    Get single farmer details + their alert history (last 10 alerts).
    Used by the farmer detail panel on the dashboard.
    """
    farmer = db.query(Farmer).filter(Farmer.id == farmer_id).first()
    if not farmer:
        raise HTTPException(status_code=404, detail="Farmer not found")

    # Get last 10 alerts
    alerts = (
        db.query(Alert)
        .filter(Alert.farmer_id == farmer_id)
        .order_by(Alert.created_at.desc())
        .limit(10)
        .all()
    )

    return {
        "farmer": {
            "id": farmer.id,
            "farmer_name": farmer.farmer_name or "Unknown",
            "village_name": farmer.village_name,
            "district": farmer.district,
            "state": farmer.state,
            "crop": farmer.crop_name,
            "latitude": farmer.latitude,
            "longitude": farmer.longitude,
            "stress_level": farmer.current_stress_level,
            "last_alert_at": farmer.last_alert_sent_at.isoformat() if farmer.last_alert_sent_at else None,
            "registered_at": farmer.registered_at.isoformat() if farmer.registered_at else None,
        },
        "alert_history": [
            {
                "id": a.id,
                "stress_level": a.stress_level,
                "ndwi": a.ndwi,
                "ndvi": a.ndvi,
                "ndmi": a.ndmi,
                "cloud_cover": a.cloud_cover,
                "days_until_stress": a.days_until_stress,
                "weather_summary": a.weather_summary,
                "message_hindi": a.alert_message_hindi,
                "message_english": a.alert_message_english,
                "satellite_date": a.satellite_image_date,
                "was_reliable": a.was_reliable,
                "data_source": a.data_source,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in alerts
        ],
    }


@router.get("/stats", dependencies=[Depends(verify_api_key)])
async def get_stats(db: Session = Depends(get_db)):
    """
    Aggregate statistics for the dashboard overview panel.
    Shows total farmers, stress distribution, alerts today.
    """
    total = db.query(func.count(Farmer.id)).scalar() or 0

    # Stress level distribution
    green = db.query(func.count(Farmer.id)).filter(Farmer.current_stress_level == "green").scalar() or 0
    yellow = db.query(func.count(Farmer.id)).filter(Farmer.current_stress_level == "yellow").scalar() or 0
    red = db.query(func.count(Farmer.id)).filter(Farmer.current_stress_level == "red").scalar() or 0

    # Alerts today
    from datetime import datetime, timezone, timedelta
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    alerts_today = (
        db.query(func.count(Alert.id))
        .filter(Alert.created_at >= today_start)
        .scalar() or 0
    )

    # Total alerts
    total_alerts = db.query(func.count(Alert.id)).scalar() or 0

    # Unique villages
    unique_villages = db.query(func.count(func.distinct(Farmer.village_name))).scalar() or 0

    return {
        "total_farmers": total,
        "stress_distribution": {
            "green": green,
            "yellow": yellow,
            "red": red,
        },
        "alerts_today": alerts_today,
        "total_alerts": total_alerts,
        "villages_covered": unique_villages,
    }
