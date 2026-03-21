from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.app.models.content import ContentItem
from backend.app.services.elasticsearch import (
    CONTENT_ITEMS_INDEX,
    PLAYBACK_STATE_INDEX,
    get_es_client,
)

router = APIRouter(prefix="/api/content", tags=["content"])


class FacetBucket(BaseModel):
    key: str
    count: int


class ContentSearchResponse(BaseModel):
    items: list[ContentItem]
    total: int
    facets: dict[str, list[FacetBucket]]


@router.get("", response_model=ContentSearchResponse)
async def list_content(
    subscription_id: str | None = None,
    content_type: str | None = None,
    consumed: str | None = None,  # "true", "false", or None for all
    q: str | None = None,
    size: int = Query(default=50, le=200),
    offset: int = 0,
):
    es = get_es_client()
    must: list[dict[str, Any]] = []
    filter_clauses: list[dict[str, Any]] = []

    if subscription_id:
        filter_clauses.append({"term": {"subscription_id": subscription_id}})
    if content_type:
        filter_clauses.append({"term": {"type": content_type}})
    if q:
        must.append({
            "multi_match": {
                "query": q,
                "fields": [
                    "title^3",
                    "summary^2",
                    "content_markdown",
                    "interest_reasoning",
                ],
                "type": "best_fields",
                "fuzziness": "AUTO",
            }
        })

    query: dict[str, Any]
    if must or filter_clauses:
        query = {"bool": {}}
        if must:
            query["bool"]["must"] = must
        if filter_clauses:
            query["bool"]["filter"] = filter_clauses
    else:
        query = {"match_all": {}}

    # Build aggregations for facets
    aggs = {
        "type": {"terms": {"field": "type", "size": 10}},
        "subscription_id": {"terms": {"field": "subscription_id", "size": 100}},
    }

    body: dict[str, Any] = {
        "query": query,
        "size": size,
        "from": offset,
        "sort": [{"published_at": {"order": "desc", "missing": "_last"}}],
        "aggs": aggs,
    }

    resp = await es.search(index=CONTENT_ITEMS_INDEX, body=body)

    # Get all item IDs from this page to look up consumed status
    hits = resp["hits"]["hits"]
    item_ids = [hit["_id"] for hit in hits]

    # Batch lookup consumed status
    consumed_set: set[str] = set()
    if item_ids:
        playback_resp = await es.search(
            index=PLAYBACK_STATE_INDEX,
            body={
                "query": {"terms": {"content_item_id": item_ids}},
                "size": len(item_ids),
                "_source": ["content_item_id", "consumed"],
            },
        )
        consumed_set = {
            h["_source"]["content_item_id"]
            for h in playback_resp["hits"]["hits"]
            if h["_source"].get("consumed")
        }

    # Also get global consumed count for the facet
    consumed_count_resp = await es.count(
        index=PLAYBACK_STATE_INDEX,
        body={"query": {"term": {"consumed": True}}},
    )
    total_consumed = consumed_count_resp["count"]

    # Build items list, applying consumed filter if requested
    items: list[ContentItem] = []
    for hit in hits:
        item = ContentItem(id=hit["_id"], **hit["_source"])
        is_consumed = hit["_id"] in consumed_set
        if consumed == "true" and not is_consumed:
            continue
        if consumed == "false" and is_consumed:
            continue
        items.append(item)

    total_hits = resp["hits"]["total"]
    total = total_hits["value"] if isinstance(total_hits, dict) else total_hits

    # Build facets from aggregations
    facets: dict[str, list[FacetBucket]] = {}
    for agg_name, agg_data in resp.get("aggregations", {}).items():
        facets[agg_name] = [
            FacetBucket(key=bucket["key"], count=bucket["doc_count"])
            for bucket in agg_data.get("buckets", [])
        ]

    # Add consumed/unwatched facet
    facets["consumed"] = [
        FacetBucket(key="unwatched", count=total - total_consumed),
        FacetBucket(key="watched", count=total_consumed),
    ]

    return ContentSearchResponse(items=items, total=total, facets=facets)


@router.get("/{item_id}", response_model=ContentItem)
async def get_content_item(item_id: str):
    es = get_es_client()
    try:
        resp = await es.get(index=CONTENT_ITEMS_INDEX, id=item_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Content item not found")
    return ContentItem(id=resp["_id"], **resp["_source"])


@router.delete("/{item_id}")
async def delete_content_item(item_id: str):
    es = get_es_client()
    await es.delete(index=CONTENT_ITEMS_INDEX, id=item_id)
    return {"deleted": item_id}
