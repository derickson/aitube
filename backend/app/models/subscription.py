from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class SubscriptionType(str, Enum):
    youtube_channel = "youtube_channel"
    podcast = "podcast"
    rss = "rss"


class SubscriptionStatus(str, Enum):
    active = "active"
    muted = "muted"
    unfollowed = "unfollowed"


class SubscriptionCreate(BaseModel):
    url: str
    name: str
    type: SubscriptionType
    description: str = ""
    interest_notes: str = ""


class SubscriptionUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    interest_notes: Optional[str] = None
    status: Optional[SubscriptionStatus] = None


class Subscription(BaseModel):
    id: str
    type: SubscriptionType
    url: str
    name: str
    description: str = ""
    interest_notes: str = ""
    status: SubscriptionStatus = SubscriptionStatus.active
    added_at: datetime
    last_polled_at: Optional[datetime] = None
