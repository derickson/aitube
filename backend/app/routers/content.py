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
    interest: str | None = None,  # "up", "down", "none", or None for all
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
    if interest == "up":
        filter_clauses.append({"term": {"user_interest": "up"}})
    elif interest == "down":
        filter_clauses.append({"term": {"user_interest": "down"}})
    elif interest == "none":
        filter_clauses.append({"bool": {"must_not": {"exists": {"field": "user_interest"}}}})
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
        "interest": {"terms": {"field": "user_interest", "size": 10}},
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


@router.put("/{item_id}/interest")
async def set_interest(item_id: str, interest: str = "up"):
    """Set interest on a content item: 'up', 'down', or 'none' to clear."""
    es = get_es_client()
    if interest == "none":
        # Remove the field by setting to None via script
        await es.update(
            index=CONTENT_ITEMS_INDEX,
            id=item_id,
            script={"source": "ctx._source.remove('user_interest')"},
        )
    else:
        await es.update(index=CONTENT_ITEMS_INDEX, id=item_id, doc={"user_interest": interest})
    return {"id": item_id, "interest": interest if interest != "none" else None}


@router.post("/playback-progress")
async def batch_playback_progress(item_ids: list[str]):
    """Get playback progress for multiple content items at once."""
    if not item_ids:
        return {}
    es = get_es_client()

    # Get playback states
    playback_resp = await es.search(
        index=PLAYBACK_STATE_INDEX,
        body={
            "query": {"terms": {"content_item_id": item_ids}},
            "size": len(item_ids),
            "_source": ["content_item_id", "position_seconds"],
        },
    )

    # Get durations from content items
    content_resp = await es.search(
        index=CONTENT_ITEMS_INDEX,
        body={
            "query": {"ids": {"values": item_ids}},
            "size": len(item_ids),
            "_source": ["duration_seconds"],
        },
    )

    durations = {
        hit["_id"]: hit["_source"].get("duration_seconds", 0) or 0
        for hit in content_resp["hits"]["hits"]
    }

    result = {}
    for hit in playback_resp["hits"]["hits"]:
        cid = hit["_source"]["content_item_id"]
        pos = hit["_source"].get("position_seconds", 0) or 0
        dur = durations.get(cid, 0)
        pct = round((pos / dur) * 100) if dur > 0 else 0
        result[cid] = {"position_seconds": pos, "duration_seconds": dur, "percent": min(pct, 100)}

    return result


@router.get("/export/csv")
async def export_csv():
    """Export all content items as CSV using ES scroll cursor."""
    from fastapi.responses import StreamingResponse
    import csv
    import io

    es = get_es_client()

    # Initial search with scroll
    resp = await es.search(
        index=CONTENT_ITEMS_INDEX,
        body={
            "query": {"match_all": {}},
            "_source": ["url", "title", "type", "duration_seconds", "subscription_id", "published_at", "consumed"],
            "sort": [{"published_at": {"order": "desc", "missing": "_last"}}],
        },
        scroll="2m",
        size=500,
    )

    async def generate():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["id", "type", "title", "url", "duration_seconds", "published_at", "consumed"])
        output.seek(0)
        yield output.read()

        nonlocal resp
        while True:
            hits = resp["hits"]["hits"]
            if not hits:
                break
            for hit in hits:
                s = hit["_source"]
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow([
                    hit["_id"],
                    s.get("type", ""),
                    s.get("title", ""),
                    s.get("url", ""),
                    s.get("duration_seconds", ""),
                    s.get("published_at", ""),
                    s.get("consumed", False),
                ])
                output.seek(0)
                yield output.read()

            scroll_id = resp.get("_scroll_id")
            if not scroll_id:
                break
            resp = await es.scroll(scroll_id=scroll_id, scroll="2m")

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=aitube-content.csv"},
    )


@router.delete("/{item_id}")
async def delete_content_item(item_id: str):
    es = get_es_client()
    await es.delete(index=CONTENT_ITEMS_INDEX, id=item_id)
    return {"deleted": item_id}
