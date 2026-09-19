from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Query
from pydantic import BaseModel

from backend.app.services.elasticsearch import WATCH_TIME_INDEX, get_es_client

router = APIRouter(prefix="/api/consumption_stats", tags=["consumption_stats"])


class HourlyBucket(BaseModel):
    hour: str  # local ISO-8601 hour start, e.g. "2026-09-19T14:00:00-07:00"
    date: str  # local date, "2026-09-19"
    hour_of_day: int  # 0-23, local
    minutes: float
    minutes_by_type: dict[str, float]


class HourlyConsumptionResponse(BaseModel):
    generated_at: str
    timezone: str
    today_total_minutes: float
    yesterday_total_minutes: float
    today: list[HourlyBucket]
    yesterday: list[HourlyBucket]


@router.get("/hourly/", response_model=HourlyConsumptionResponse)
async def hourly_consumption(
    tz: str = Query(default="UTC", description="IANA timezone name, e.g. America/Los_Angeles"),
):
    """Minutes of content consumed per hour, for today and yesterday in the
    given timezone. Backed by aitube-watch-time, which the playback endpoint
    accumulates into as playhead updates arrive (see watch_time_tracker.py).
    Intended to be polled periodically (e.g. every 15 minutes) to track daily
    consumption as it happens.
    """
    try:
        zone = ZoneInfo(tz)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
        tz = "UTC"

    now_local = datetime.now(zone)
    today_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start_local = today_start_local - timedelta(days=1)
    range_start_utc = yesterday_start_local.astimezone(timezone.utc)
    range_end_utc = (today_start_local + timedelta(days=1)).astimezone(timezone.utc)

    es = get_es_client()
    resp = await es.search(
        index=WATCH_TIME_INDEX,
        body={
            "query": {
                "range": {
                    "hour": {
                        "gte": range_start_utc.isoformat(),
                        "lt": range_end_utc.isoformat(),
                    }
                }
            },
            "sort": [{"hour": {"order": "asc"}}],
            "size": 100,
        },
    )
    docs_by_hour_utc = {hit["_source"]["hour"]: hit["_source"] for hit in resp["hits"]["hits"]}

    today: list[HourlyBucket] = []
    yesterday: list[HourlyBucket] = []
    today_date = today_start_local.date().isoformat()
    yesterday_date = yesterday_start_local.date().isoformat()

    slot = range_start_utc
    while slot < range_end_utc:
        doc = docs_by_hour_utc.get(slot.isoformat(), {})
        local_dt = slot.astimezone(zone)
        bucket = HourlyBucket(
            hour=local_dt.isoformat(),
            date=local_dt.date().isoformat(),
            hour_of_day=local_dt.hour,
            minutes=round(doc.get("total_seconds", 0.0) / 60, 1),
            minutes_by_type={
                k: round(v / 60, 1) for k, v in (doc.get("by_type") or {}).items()
            },
        )
        if bucket.date == today_date:
            today.append(bucket)
        elif bucket.date == yesterday_date:
            yesterday.append(bucket)
        slot += timedelta(hours=1)

    return HourlyConsumptionResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        timezone=tz,
        today_total_minutes=round(sum(b.minutes for b in today), 1),
        yesterday_total_minutes=round(sum(b.minutes for b in yesterday), 1),
        today=today,
        yesterday=yesterday,
    )
