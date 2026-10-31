"""Dopamine Detox: a timed lock that stops YouTube videos from playing.

State is deliberately held in process memory only: there is no way to end a
detox early except restarting the AITube backend. It applies to every browser
(unlike Demo/Zen Mode, which are per-browser). The backend only owns the clock;
the frontend enforces it by not rendering the YouTube player (podcasts,
articles and Corridor videos are unaffected).
"""

from datetime import datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

Duration = Literal["3h", "1d", "3d"]

EASTERN = ZoneInfo("America/New_York")
RELEASE_TIME = time(2, 0)  # 2am Eastern

_until: datetime | None = None


def compute_until(duration: Duration, now: datetime) -> datetime:
    """When a detox started at `now` ends.

    3h: 240 minutes from now. 1d: 2am Eastern on the next calendar day.
    3d: 2am Eastern after three midnights have passed.
    """
    if duration == "3h":
        return now + timedelta(minutes=240)
    days = {"1d": 1, "3d": 3}[duration]
    local_date = now.astimezone(EASTERN).date() + timedelta(days=days)
    return datetime.combine(local_date, RELEASE_TIME, tzinfo=EASTERN).astimezone(timezone.utc)


def active_until(now: datetime | None = None) -> datetime | None:
    now = now or datetime.now(timezone.utc)
    return _until if _until is not None and _until > now else None


def start(duration: Duration, now: datetime | None = None) -> datetime:
    """Start a detox. Never shortens one already running."""
    global _until
    now = now or datetime.now(timezone.utc)
    new_until = compute_until(duration, now)
    current = active_until(now)
    _until = max(current, new_until) if current else new_until
    return _until
