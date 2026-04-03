from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel


class ContentType(str, Enum):
    video = "video"
    podcast_episode = "podcast_episode"
    article = "article"


class TranscriptChunk(BaseModel):
    text: str
    start: float
    end: float


class Transcript(BaseModel):
    text: str
    chunks: list[TranscriptChunk] = []


class ContentItem(BaseModel):
    id: str
    subscription_id: str
    external_id: str
    type: ContentType
    title: str
    url: str
    published_at: Optional[datetime] = None
    discovered_at: datetime
    duration_seconds: Optional[float] = None
    thumbnail_url: Optional[str] = ""
    summary: Optional[str] = ""
    interest_score: Optional[float] = None
    interest_reasoning: Optional[str] = ""
    transcript: Optional[Transcript] = None
    consumed: bool = False
    viewed: bool = False
    user_interest: Optional[str] = None  # "up", "down", or null
    content_markdown: str = ""
    content_dlp_cache_id: str = ""
    metadata: dict[str, Any] = {}
