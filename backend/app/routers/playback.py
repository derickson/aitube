from datetime import datetime, timezone

from fastapi import APIRouter

from backend.app.models.playback import PlaybackState, PlaybackUpdate
from backend.app.services.elasticsearch import (
    CONTENT_ITEMS_INDEX,
    PLAYBACK_STATE_INDEX,
    get_es_client,
)
from backend.app.services import content_cache
from backend.app.services.playback_buffer import playback_buffer

router = APIRouter(prefix="/api/playback", tags=["playback"])


@router.get("/{content_item_id}/", response_model=PlaybackState | None)
async def get_playback(content_item_id: str):
    # Check in-memory buffer first for the most recent uncommitted state
    buffered = playback_buffer.get(content_item_id)
    if buffered:
        return PlaybackState(**buffered)

    es = get_es_client()

    # Try direct ID lookup (docs written by the buffer flush use content_item_id as _id)
    try:
        resp = await es.get(index=PLAYBACK_STATE_INDEX, id=content_item_id)
        return PlaybackState(**resp["_source"])
    except Exception:
        pass

    # Fall back to field search for legacy docs with auto-generated IDs
    resp = await es.search(
        index=PLAYBACK_STATE_INDEX,
        body={
            "query": {"term": {"content_item_id": content_item_id}},
            "sort": [{"last_updated_at": {"order": "desc"}}],
            "size": 1,
        },
    )
    hits = resp["hits"]["hits"]
    if not hits:
        return None
    return PlaybackState(**hits[0]["_source"])


@router.put("/{content_item_id}/", response_model=PlaybackState)
async def update_playback(content_item_id: str, data: PlaybackUpdate):
    es = get_es_client()

    # Round to 5-second granularity
    position = round(data.position_seconds / 5) * 5
    now = datetime.now(timezone.utc)

    # Check if content item has a duration to determine consumed status
    consumed = False
    try:
        item_resp = await es.get(index=CONTENT_ITEMS_INDEX, id=content_item_id)
        duration = item_resp["_source"].get("duration_seconds")
        # Backfill duration from player if missing
        if not duration and data.duration_seconds and data.duration_seconds > 0:
            duration = data.duration_seconds
            await es.update(index=CONTENT_ITEMS_INDEX, id=content_item_id, doc={"duration_seconds": duration})
        if duration and duration > 0:
            consumed = position >= (duration * 0.9)
        # Update consumed flag on the content item itself
        prev_consumed = item_resp["_source"].get("consumed", False)
        if consumed and not prev_consumed:
            await es.update(index=CONTENT_ITEMS_INDEX, id=content_item_id, doc={"consumed": True})
            content_cache.invalidate()
    except Exception:
        pass

    doc = {
        "content_item_id": content_item_id,
        "position_seconds": position,
        "consumed": consumed,
        "last_updated_at": now.isoformat(),
    }

    # Buffer the playback state — flushed to ES every 15 minutes and on shutdown
    playback_buffer.update(content_item_id, doc)

    return PlaybackState(**doc)
