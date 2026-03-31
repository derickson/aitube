import asyncio
import logging
import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel

from backend.app.models.content import ContentType
from backend.app.services.elasticsearch import CONTENT_ITEMS_INDEX, get_es_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["watchlist"])


class WatchlistItem(BaseModel):
    id: str
    subscription_id: str
    external_id: str
    type: ContentType
    title: str
    url: str
    published_at: datetime | None = None
    discovered_at: datetime
    duration_seconds: float | None = None
    thumbnail_url: str | None = ""
    interest_score: float | None = None
    interest_reasoning: str | None = ""
    consumed: bool = False
    user_interest: str | None = None
    content_dlp_cache_id: str = ""
    metadata: dict = {}


_WATCHLIST_EXCLUDES = ["summary", "transcript", "content_markdown", "metadata.description"]

# Track background tasks to prevent GC
_background_tasks: set[asyncio.Task] = set()

_YT_VIDEO_ID_RE = re.compile(
    r"(?:v=|youtu\.be/|embed/|shorts/)([a-zA-Z0-9_-]{11})"
)


def _extract_video_id(url: str) -> str | None:
    m = _YT_VIDEO_ID_RE.search(url)
    return m.group(1) if m else None


@router.get("/watchlist/", response_model=list[WatchlistItem])
async def get_watchlist(
    size: int = Query(default=50, le=200),
    offset: int = 0,
):
    """Return unwatched YouTube videos sorted by published_at desc."""
    es = get_es_client()
    resp = await es.search(
        index=CONTENT_ITEMS_INDEX,
        body={
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"type": "video"}},
                        {"bool": {"should": [
                            {"term": {"consumed": False}},
                            {"bool": {"must_not": {"exists": {"field": "consumed"}}}},
                        ]}},
                    ]
                }
            },
            "_source": {"excludes": _WATCHLIST_EXCLUDES},
            "size": size,
            "from": offset,
            "sort": [{"published_at": {"order": "desc", "missing": "_last"}}],
        },
    )
    return [
        WatchlistItem(id=hit["_id"], **hit["_source"])
        for hit in resp["hits"]["hits"]
    ]


class AddVideosRequest(BaseModel):
    urls: list[str]


class AddVideosResponse(BaseModel):
    accepted: list[str]
    skipped: list[str]
    errors: list[str]


@router.post("/submit_video/", response_model=AddVideosResponse, tags=["watchlist"])
async def add_videos(request: AddVideosRequest):
    """Accept YouTube URLs for background processing. Returns immediately."""
    es = get_es_client()

    # Parse video IDs from URLs
    parsed: list[tuple[str, str]] = []  # (video_id, url)
    errors: list[str] = []
    for url in request.urls:
        vid = _extract_video_id(url)
        if vid:
            parsed.append((vid, url))
        else:
            errors.append(url)

    # Check which already exist in ES
    if parsed:
        external_ids = [f"yt_{vid}" for vid, _ in parsed]
        existing_resp = await es.search(
            index=CONTENT_ITEMS_INDEX,
            body={
                "query": {"terms": {"external_id": external_ids}},
                "_source": ["external_id"],
                "size": len(external_ids),
            },
        )
        existing = {
            hit["_source"]["external_id"]
            for hit in existing_resp["hits"]["hits"]
        }
    else:
        existing = set()

    accepted = []
    skipped = []
    for vid, url in parsed:
        if f"yt_{vid}" in existing:
            skipped.append(url)
        else:
            accepted.append(url)

    # Fire background processing for accepted URLs
    if accepted:
        task = asyncio.create_task(_process_adhoc_videos(accepted))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    return AddVideosResponse(accepted=accepted, skipped=skipped, errors=errors)


async def _process_adhoc_videos(urls: list[str]) -> None:
    """Background task: process each URL through the YouTube pipeline."""
    from backend.app.services.feed_poller import (
        build_adhoc_youtube_doc,
        process_youtube_video_doc,
    )

    es = get_es_client()
    for url in urls:
        vid = _extract_video_id(url)
        if not vid:
            continue
        try:
            doc = build_adhoc_youtube_doc(vid, url)
            enriched = await process_youtube_video_doc(doc)
            if enriched is None:
                logger.info("Skipped ad-hoc video (livestream?): %s", url)
                continue
            doc_id = str(uuid.uuid4())
            await es.index(
                index=CONTENT_ITEMS_INDEX, id=doc_id, document=enriched
            )
            logger.info("Indexed ad-hoc video '%s' as %s", enriched.get("title", url), doc_id)
        except Exception:
            logger.exception("Failed to process ad-hoc video: %s", url)
