from datetime import datetime

from pydantic import BaseModel


class PlaybackState(BaseModel):
    content_item_id: str
    position_seconds: float = 0.0
    consumed: bool = False
    last_updated_at: datetime


class PlaybackUpdate(BaseModel):
    position_seconds: float
    duration_seconds: float | None = None
