from fastapi import APIRouter, HTTPException, Query

from backend.app.models.content import ContentItem
from backend.app.services.elasticsearch import (
    CONTENT_ITEMS_INDEX,
    PLAYBACK_STATE_INDEX,
    get_es_client,
)

router = APIRouter(prefix="/api/content", tags=["content"])


@router.get("", response_model=list[ContentItem])
async def list_content(
    subscription_id: str | None = None,
    content_type: str | None = None,
    unwatched_only: bool = False,
    q: str | None = None,
    size: int = Query(default=50, le=200),
    offset: int = 0,
):
    es = get_es_client()
    must = []

    if subscription_id:
        must.append({"term": {"subscription_id": subscription_id}})
    if content_type:
        must.append({"term": {"type": content_type}})
    if q:
        must.append({
            "multi_match": {
                "query": q,
                "fields": ["title^3", "summary^2", "content_markdown"],
            }
        })

    query = {"bool": {"must": must}} if must else {"match_all": {}}

    resp = await es.search(
        index=CONTENT_ITEMS_INDEX,
        body={
            "query": query,
            "size": size,
            "from": offset,
            "sort": [{"published_at": {"order": "desc", "missing": "_last"}}],
        },
    )

    items = []
    for hit in resp["hits"]["hits"]:
        items.append(ContentItem(id=hit["_id"], **hit["_source"]))

    if unwatched_only:
        item_ids = [item.id for item in items]
        if item_ids:
            playback_resp = await es.search(
                index=PLAYBACK_STATE_INDEX,
                body={
                    "query": {"bool": {
                        "must": [
                            {"terms": {"content_item_id": item_ids}},
                            {"term": {"consumed": True}},
                        ]
                    }},
                    "size": len(item_ids),
                },
            )
            consumed_ids = {
                hit["_source"]["content_item_id"]
                for hit in playback_resp["hits"]["hits"]
            }
            items = [i for i in items if i.id not in consumed_ids]

    return items


@router.get("/{item_id}", response_model=ContentItem)
async def get_content_item(item_id: str):
    es = get_es_client()
    try:
        resp = await es.get(index=CONTENT_ITEMS_INDEX, id=item_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Content item not found")
    return ContentItem(id=resp["_id"], **resp["_source"])
