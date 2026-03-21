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
    if consumed == "true":
        filter_clauses.append({"term": {"consumed": True}})
    elif consumed == "false":
        filter_clauses.append({"bool": {"should": [
            {"term": {"consumed": False}},
            {"bool": {"must_not": {"exists": {"field": "consumed"}}}},
        ]}})
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

    # Filtered search for results
    search_body: dict[str, Any] = {
        "query": query,
        "size": size,
        "from": offset,
        "sort": [{"published_at": {"order": "desc", "missing": "_last"}}],
    }

    # Unfiltered aggregation for global facet counts
    global_aggs = {
        "type": {"terms": {"field": "type", "size": 10}},
        "subscription_id": {"terms": {"field": "subscription_id", "size": 100}},
        "consumed": {"terms": {"field": "consumed", "missing": False}},
    }
    aggs_body: dict[str, Any] = {
        "size": 0,
        "aggs": global_aggs,
    }

    import asyncio
    search_resp, aggs_resp = await asyncio.gather(
        es.search(index=CONTENT_ITEMS_INDEX, body=search_body),
        es.search(index=CONTENT_ITEMS_INDEX, body=aggs_body),
    )

    hits = search_resp["hits"]["hits"]
    items: list[ContentItem] = []
    for hit in hits:
        items.append(ContentItem(id=hit["_id"], **hit["_source"]))

    total_hits = search_resp["hits"]["total"]
    total = total_hits["value"] if isinstance(total_hits, dict) else total_hits

    # Build facets from unfiltered aggregations
    facets: dict[str, list[FacetBucket]] = {}
    for agg_name, agg_data in aggs_resp.get("aggregations", {}).items():
        if agg_name == "consumed":
            watched = 0
            unwatched = 0
            for bucket in agg_data.get("buckets", []):
                key = bucket.get("key_as_string", str(bucket.get("key", "")))
                if key in ("true", "1", "True"):
                    watched = bucket["doc_count"]
                else:
                    unwatched = bucket["doc_count"]
            facets["consumed"] = [
                FacetBucket(key="unwatched", count=unwatched),
                FacetBucket(key="watched", count=watched),
            ]
        else:
            facets[agg_name] = [
                FacetBucket(key=bucket["key"], count=bucket["doc_count"])
                for bucket in agg_data.get("buckets", [])
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


@router.post("/{item_id}/transcribe")
async def transcribe_content_item(item_id: str):
    """Trigger transcription for a content item. Downloads audio and runs local Parakeet TDT."""
    from backend.app.services import content_dlp

    es = get_es_client()
    try:
        resp = await es.get(index=CONTENT_ITEMS_INDEX, id=item_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Content item not found")

    item = resp["_source"]
    item_type = item.get("type")
    url = item.get("url", "")

    if not url:
        raise HTTPException(status_code=400, detail="No URL to transcribe")

    try:
        if item_type == "video":
            raw = await content_dlp.fetch_youtube(url, no_audio=False, transcript=True)
        elif item_type == "podcast_episode":
            extras = item.get("metadata", {}).get("extras", {})
            audio_url = extras.get("enclosure_url", url)
            raw = await content_dlp.download_and_transcribe(audio_url)
        else:
            raise HTTPException(status_code=400, detail=f"Cannot transcribe type: {item_type}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")

    # Extract transcript from result
    transcript = None
    if raw.get("transcript"):
        t = raw["transcript"]
        if isinstance(t, dict):
            transcript = t
        elif isinstance(t, str):
            transcript = {"text": t, "chunks": []}

    if not transcript:
        raise HTTPException(status_code=500, detail="No transcript produced")

    # Update the ES document
    await es.update(
        index=CONTENT_ITEMS_INDEX,
        id=item_id,
        doc={"transcript": transcript},
    )

    return {"status": "ok", "transcript_length": len(transcript.get("text", ""))}


@router.put("/{item_id}/consumed")
async def set_consumed(item_id: str, consumed: bool = True):
    es = get_es_client()
    await es.update(index=CONTENT_ITEMS_INDEX, id=item_id, doc={"consumed": consumed})
    return {"id": item_id, "consumed": consumed}


@router.delete("/{item_id}")
async def delete_content_item(item_id: str):
    es = get_es_client()
    await es.delete(index=CONTENT_ITEMS_INDEX, id=item_id)
    return {"deleted": item_id}
