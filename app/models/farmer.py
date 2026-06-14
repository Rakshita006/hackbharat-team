"""
JalSense 2.0 — Farmer Model

One row per unique phone number. If the same farmer messages again
with a different crop/village, we UPDATE their record and run a fresh analysis.
The current_stress_level field powers the dashboard map (green/yellow/red dots).
"""

from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, DateTime
from sqlalchemy.orm import relationship
from app.database import Base


class Farmer(Base):
    __tablename__ = "farmers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    phone_number = Column(String(15), unique=True, nullable=False, index=True)
    farmer_name = Column(String(100), nullable=True)  # WhatsApp profile name (not always available)

    # Location
    village_name = Column(String(100), nullable=False)
    district = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)

    # Crop
    crop_name = Column(String(50), nullable=False)  # Internal English name: "wheat", "rice", etc.

    # Current status (updated on every new analysis)
    current_stress_level = Column(String(10), nullable=True)  # "green" / "yellow" / "red"

    # Timestamps
    last_alert_sent_at = Column(DateTime, nullable=True)
    registered_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Relationship: one farmer has many alerts
    alerts = relationship("Alert", back_populates="farmer", order_by="Alert.created_at.desc()")

    def __repr__(self):
        return (
            f"<Farmer(id={self.id}, phone='{self.phone_number}', "
            f"village='{self.village_name}', crop='{self.crop_name}', "
            f"stress='{self.current_stress_level}')>"
        )
