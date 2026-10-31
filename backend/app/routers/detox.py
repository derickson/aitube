"""Dopamine Detox endpoints. There is intentionally no endpoint to end a detox."""

from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel

from backend.app.services import detox

router = APIRouter(prefix="/api/detox", tags=["detox"])


class DetoxStatus(BaseModel):
    active: bool
    until: datetime | None
    remaining_seconds: int


class DetoxStart(BaseModel):
    duration: detox.Duration


def _status() -> DetoxStatus:
    now = datetime.now(timezone.utc)
    until = detox.active_until(now)
    remaining = int((until - now).total_seconds()) if until else 0
    return DetoxStatus(active=until is not None, until=until, remaining_seconds=remaining)


@router.get("/", response_model=DetoxStatus)
async def get_detox():
    return _status()


@router.post("/", response_model=DetoxStatus)
async def start_detox(body: DetoxStart):
    detox.start(body.duration)
    return _status()
