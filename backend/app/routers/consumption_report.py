from fastapi import APIRouter, Query
from pydantic import BaseModel

from backend.app.services.elasticsearch import (
    CONTENT_ITEMS_INDEX,
    PLAYBACK_STATE_INDEX,
    get_es_client,
)

router = APIRouter(prefix="/api/consumption_report", tags=["consumption_report"])


class ConsumptionReportItem(BaseModel):
    subscription_id: str
    content_item_id: str
    content_type: str
    title: str
    published_at: str | None
    consumed: bool
    viewed: bool
    watch_percentage: float | None
    interest: str | None  # "up", "down", or null


@router.get("/", response_model=list[ConsumptionReportItem])
async def consumption_report(
    subscription_id: str | None = None,
    content_item_id: str | None = None,
    content_type: str | None = None,
    size: int = Query(default=100, le=500),
):
    es = get_es_client()

    # Build content items query
    filters: list[dict] = []
    if subscription_id:
        filters.append({"term": {"subscription_id": subscription_id}})
    if content_type:
        filters.append({"term": {"type": content_type}})

    query: dict = {"bool": {"filter": filters}} if filters else {"match_all": {}}

    # If filtering by specific content item, use IDs query
    if content_item_id:
        id_filter = {"ids": {"values": [content_item_id]}}
        if filters:
            filters.append(id_filter)
        else:
            query = id_filter

    resp = await es.search(
        index=CONTENT_ITEMS_INDEX,
        body={
            "query": query,
            "sort": [{"published_at": {"order": "desc", "missing": "_last"}}],
            "size": size,
            "_source": [
                "subscription_id",
                "type",
                "title",
                "published_at",
                "consumed",
                "viewed",
                "user_interest",
                "duration_seconds",
            ],
        },
    )

    hits = resp["hits"]["hits"]
    if not hits:
        return []

    # Batch-fetch playback states
    item_ids = [h["_id"] for h in hits]
    playback_resp = await es.search(
        index=PLAYBACK_STATE_INDEX,
        body={
            "query": {"terms": {"content_item_id": item_ids}},
            "size": len(item_ids),
            "_source": ["content_item_id", "position_seconds"],
        },
    )
    playback_map: dict[str, float] = {}
    for ph in playback_resp["hits"]["hits"]:
        src = ph["_source"]
        cid = src.get("content_item_id")
        pos = src.get("position_seconds", 0)
        # Keep the highest position if multiple records exist
        if cid and pos > playback_map.get(cid, 0):
            playback_map[cid] = pos

    # Build response
    items: list[ConsumptionReportItem] = []
    for hit in hits:
        src = hit["_source"]
        doc_id = hit["_id"]
        duration = src.get("duration_seconds")
        position = playback_map.get(doc_id)

        watch_pct = None
        if position is not None and duration and duration > 0:
            watch_pct = min(round(position / duration * 100, 1), 100.0)

        items.append(
            ConsumptionReportItem(
                subscription_id=src.get("subscription_id", ""),
                content_item_id=doc_id,
                content_type=src.get("type", ""),
                title=src.get("title", ""),
                published_at=src.get("published_at"),
                consumed=src.get("consumed", False),
                viewed=src.get("viewed", False),
                watch_percentage=watch_pct,
                interest=src.get("user_interest"),
            )
        )

    return items
