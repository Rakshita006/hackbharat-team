import logging

from fastapi import APIRouter, HTTPException, Request, Body
from pydantic import BaseModel, Field

from app.services.pipeline import run_demo_pipeline
from app.utils.crop_data import resolve_crop_name, VALID_CROPS
from app.utils import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Demo"])


class DemoRequest(BaseModel):
    # Validates demo request payload
    village: str = Field(
        ..., min_length=2, max_length=100,
        description="Village name (e.g., 'Chitrakoot', 'Rampur')",
        examples=["Chitrakoot"]
    )
    crop: str = Field(
        ..., min_length=2, max_length=50,
        description="Crop name in Hindi or English (e.g., 'gehun', 'wheat')",
        examples=["gehun"]
    )
    crop_age_days: int = Field(
        45, ge=1, le=365,
        description="Optional crop age in days for growth stage coefficient scaling",
        examples=[45]
    )


@router.post("/demo")
@limiter.limit("10/minute")
async def run_demo(request: Request, payload: DemoRequest = Body(...)):
    # Runs simulated pipeline showing intermediate satellite and weather indexes.
    resolved_crop = resolve_crop_name(payload.crop)
    if not resolved_crop:
        raise HTTPException(
            status_code=400,
            detail={
                "error": f"Unknown crop: '{payload.crop}'",
                "supported_crops": sorted(VALID_CROPS),
                "hint": "Try: wheat, gehun, rice, dhan, maize, makka, cotton, kapas",
            }
        )

    result = await run_demo_pipeline(payload.village, resolved_crop, payload.crop_age_days)


    if not result.success:
        raise HTTPException(
            status_code=404,
            detail={"error": result.error or "Pipeline failed"}
        )

    return result.to_demo_response()
