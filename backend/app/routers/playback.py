from datetime import datetime, timezone

from fastapi import APIRouter

from backend.app.models.playback import PlaybackState, PlaybackUpdate
from backend.app.services.elasticsearch import (
    CONTENT_ITEMS_INDEX,
    PLAYBACK_STATE_INDEX,
    get_es_client,
)

router = APIRouter(prefix="/api/playback", tags=["playback"])


@router.get("/{content_item_id}", response_model=PlaybackState | None)
async def get_playback(content_item_id: str):
    es = get_es_client()
    resp = await es.search(
        index=PLAYBACK_STATE_INDEX,
        body={
            "query": {"term": {"content_item_id": content_item_id}},
            "size": 1,
        },
    )
    hits = resp["hits"]["hits"]
    if not hits:
        return None
    return PlaybackState(**hits[0]["_source"])


@router.put("/{content_item_id}", response_model=PlaybackState)
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
        if duration and duration > 0:
            consumed = position >= (duration * 0.9)
        # Update consumed flag on the content item itself
        prev_consumed = item_resp["_source"].get("consumed", False)
        if consumed and not prev_consumed:
            await es.update(index=CONTENT_ITEMS_INDEX, id=content_item_id, doc={"consumed": True})
    except Exception:
        pass

    doc = {
        "content_item_id": content_item_id,
        "position_seconds": position,
        "consumed": consumed,
        "last_updated_at": now.isoformat(),
    }

    # Upsert by content_item_id
    resp = await es.search(
        index=PLAYBACK_STATE_INDEX,
        body={"query": {"term": {"content_item_id": content_item_id}}, "size": 1},
    )
    hits = resp["hits"]["hits"]
    if hits:
        await es.update(index=PLAYBACK_STATE_INDEX, id=hits[0]["_id"], doc=doc)
    else:
        await es.index(index=PLAYBACK_STATE_INDEX, document=doc)

    return PlaybackState(**doc)
