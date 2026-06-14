"""
JalSense 2.0 — Alert Model

Every pipeline run creates one alert record. This gives us:
- History: dashboard shows "last N alerts" for any farmer
- Trends: track NDWI decline over time
- Audit: reconstruct exactly what data we had when we sent an alert
- Data source tracking: know if alert was from live GEE, cache, or weather-only
"""

from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from app.database import Base


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    farmer_id = Column(Integer, ForeignKey("farmers.id"), nullable=False, index=True)

    # Satellite indices (nullable — None when satellite data unavailable)
    ndwi = Column(Float, nullable=True)
    ndvi = Column(Float, nullable=True)
    ndmi = Column(Float, nullable=True)
    cloud_cover = Column(Float, nullable=True)

    # Prediction output
    stress_level = Column(String(10), nullable=False)  # "green" / "yellow" / "red" / "unknown"
    days_until_stress = Column(Integer, nullable=True)

    # Context
    weather_summary = Column(Text, nullable=True)
    alert_message_hindi = Column(Text, nullable=False)
    alert_message_english = Column(Text, nullable=True)

    # Metadata
    satellite_image_date = Column(String(20), nullable=True)  # ISO date of the Sentinel-2 image used
    was_reliable = Column(Boolean, nullable=False, default=True)  # False if cloud cover > 20% or cached
    data_source = Column(
        String(20), nullable=False, default="live"
    )  # "live" | "cached" | "demo_precomputed" | "weather_only"

    # Timestamps
    created_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Relationship back to farmer
    farmer = relationship("Farmer", back_populates="alerts")

    def __repr__(self):
        return (
            f"<Alert(id={self.id}, farmer_id={self.farmer_id}, "
            f"stress='{self.stress_level}', source='{self.data_source}')>"
        )
