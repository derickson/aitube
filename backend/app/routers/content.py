import asyncio
import bisect
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.app.config import settings
from backend.app.models.content import ContentItem, ContentItemSummary
from backend.app.services import content_cache
from backend.app.services.elasticsearch import (
    CONTENT_ITEMS_INDEX,
    PLAYBACK_STATE_INDEX,
    QUARANTINE_EVENTS_INDEX,
    get_es_client,
)

router = APIRouter(prefix="/api/content", tags=["content"])


class FacetBucket(BaseModel):
    key: str
    count: int


class ContentSearchResponse(BaseModel):
    items: list[ContentItemSummary]
    total: int
    facets: dict[str, list[FacetBucket]]


@router.get("/", response_model=ContentSearchResponse)
async def list_content(
    subscription_id: str | None = None,
    content_type: str | None = None,
    consumed: str | None = None,  # "true", "false", or None for all
    interest: str | None = None,  # "up", "down", "none", or None for all
    q: str | None = None,
    sort: str = "date",  # "date" or "relevance"
    size: int = Query(default=50, le=200),
    offset: int = 0,
):
    cache_params = {
        "subscription_id": subscription_id, "content_type": content_type,
        "consumed": consumed, "interest": interest, "q": q,
        "sort": sort, "size": size, "offset": offset,
    }
    cached = content_cache.get(cache_params)
    if cached is not None:
        return cached

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
    lexical_match: dict[str, Any] | None = None
    if q:
        lexical_match = {
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
        }
        must.append(lexical_match)

    use_semantic = bool(
        q and sort == "relevance" and settings.enable_semantic_search
    )

    common_source = {
        "includes": [
            "subscription_id", "external_id", "type", "title", "url",
            "published_at", "discovered_at", "duration_seconds",
            "thumbnail_url", "summary", "summary_error_code", "interest_score",
            "user_interest", "consumed", "viewed", "engagement",
        ]
    }
    common_aggs = {
        "type": {"terms": {"field": "type", "size": 10}},
        "subscription_id": {"terms": {"field": "subscription_id", "size": 100}},
        "consumed": {"terms": {"field": "consumed", "missing": False}},
        "interest": {"terms": {"field": "user_interest", "size": 10}},
    }

    search_body: dict[str, Any]
    if use_semantic and lexical_match is not None:
        def _wrap(inner: dict[str, Any]) -> dict[str, Any]:
            if filter_clauses:
                return {"bool": {"must": [inner], "filter": filter_clauses}}
            return inner

        search_body = {
            "retriever": {
                "rrf": {
                    "retrievers": [
                        {"standard": {"query": _wrap(lexical_match)}},
                        {"standard": {"query": _wrap({
                            "semantic": {"field": "semantic_headline", "query": q}
                        })}},
                        {"standard": {"query": _wrap({
                            "semantic": {"field": "semantic_body", "query": q}
                        })}},
                    ],
                    "rank_window_size": max(50, size + offset),
                    "rank_constant": 60,
                }
            },
            "size": size,
            "from": offset,
            "_source": common_source,
            "aggs": common_aggs,
        }
    else:
        query: dict[str, Any]
        if must or filter_clauses:
            query = {"bool": {}}
            if must:
                query["bool"]["must"] = must
            if filter_clauses:
                query["bool"]["filter"] = filter_clauses
        else:
            query = {"match_all": {}}

        if sort == "relevance" and q:
            sort_clause: list[dict[str, Any]] = [
                {"_score": {"order": "desc"}},
                {"published_at": {"order": "desc", "missing": "_last"}},
            ]
        else:
            sort_clause = [{"published_at": {"order": "desc", "missing": "_last"}}]

        search_body = {
            "query": query,
            "size": size,
            "from": offset,
            "sort": sort_clause,
            "_source": common_source,
            "aggs": common_aggs,
        }

    search_resp = await es.search(index=CONTENT_ITEMS_INDEX, body=search_body)

    hits = search_resp["hits"]["hits"]
    items: list[ContentItemSummary] = []
    for hit in hits:
        items.append(ContentItemSummary(id=hit["_id"], **hit["_source"]))

    total_hits = search_resp["hits"]["total"]
    total = total_hits["value"] if isinstance(total_hits, dict) else total_hits

    # Build facets from filtered aggregations
    facets: dict[str, list[FacetBucket]] = {}
    for agg_name, agg_data in search_resp.get("aggregations", {}).items():
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

    response = ContentSearchResponse(items=items, total=total, facets=facets)
    content_cache.put(cache_params, response)
    return response


class PredictionResponse(BaseModel):
    interesting: list[ContentItemSummary]
    not_interesting: list[ContentItemSummary]
    total_unwatched: int
    scored: int
    unscored: int


@router.get("/predictions/", response_model=PredictionResponse)
async def predictions(limit: int | None = Query(default=None, le=500)):
    """ML-ranked watchlist split.

    Top section: every unwatched video predicted interesting (P(engaged) > 50%,
    i.e. engagement == "engaged") OR explicitly marked interesting
    (user_interest == "up").
    Bottom section: every unwatched video predicted not interesting (P(engaged)
    <= 50%) OR explicitly marked not interested (user_interest == "down").
    User marks always win and are pinned to the top of their section. Pass
    `limit` to cap each section; omit it to return all matching videos.
    """
    es = get_es_client()

    unwatched = {
        "bool": {
            "should": [
                {"term": {"consumed": False}},
                {"bool": {"must_not": {"exists": {"field": "consumed"}}}},
            ],
            "minimum_should_match": 1,
        }
    }
    base_filter = [{"term": {"type": "video"}}, unwatched]

    resp = await es.search(
        index=CONTENT_ITEMS_INDEX,
        body={
            "query": {"bool": {"filter": base_filter}},
            "size": 1000,
            "_source": {
                "includes": [
                    "subscription_id", "external_id", "type", "title", "url",
                    "published_at", "discovered_at", "duration_seconds",
                    "thumbnail_url", "summary", "summary_error_code", "interest_score",
                    "user_interest", "consumed", "viewed", "engagement",
                ]
            },
        },
    )
    hits = resp["hits"]["hits"]

    candidates: list[tuple[ContentItemSummary, float | None, bool | None]] = []
    scored = 0
    for hit in hits:
        item = ContentItemSummary(id=hit["_id"], **hit["_source"])
        eng = item.engagement
        score = eng.score if eng else None
        if score is not None:
            scored += 1
        # Treat prediction label if present, else threshold the score at 0.5.
        if eng and eng.prediction:
            predicted_engaged: bool | None = eng.prediction == "engaged"
        elif score is not None:
            predicted_engaged = score >= 0.5
        else:
            predicted_engaged = None
        candidates.append((item, score, predicted_engaged))

    interesting: list[tuple[ContentItemSummary, float | None, bool]] = []
    not_interesting: list[tuple[ContentItemSummary, float | None, bool]] = []
    for item, score, predicted_engaged in candidates:
        marked_up = item.user_interest == "up"
        marked_down = item.user_interest == "down"
        # User marks override the model.
        if marked_up:
            interesting.append((item, score, True))
        elif marked_down:
            not_interesting.append((item, score, True))
        elif predicted_engaged is True:
            interesting.append((item, score, False))
        elif predicted_engaged is False:
            not_interesting.append((item, score, False))
        # else: unscored & unmarked -> excluded from both

    # Interesting: user-marked first, then highest P(engaged) first.
    interesting.sort(key=lambda t: (0 if t[2] else 1, -(t[1] if t[1] is not None else -1.0)))
    # Not interesting: user-marked first, then lowest P(engaged) first.
    not_interesting.sort(key=lambda t: (0 if t[2] else 1, t[1] if t[1] is not None else 2.0))

    interesting_items = [t[0] for t in interesting]
    not_interesting_items = [t[0] for t in not_interesting]
    if limit is not None:
        interesting_items = interesting_items[:limit]
        not_interesting_items = not_interesting_items[:limit]

    return PredictionResponse(
        interesting=interesting_items,
        not_interesting=not_interesting_items,
        total_unwatched=len(candidates),
        scored=scored,
        unscored=len(candidates) - scored,
    )


class QuarantineEventDetail(BaseModel):
    content_item_id: str
    external_id: str | None = None
    title: str = ""
    url: str = ""
    type: str | None = None
    subscription_id: str | None = None
    created_at: str
    discovered_at: str | None = None
    reason_code: str
    reason: str = ""
    source: str | None = None
    interest_score: float | None = None
    interest_percentile: float | None = None
    engagement_score: float | None = None
    engagement_percentile: float | None = None
    verdict: str  # "fooled" | "split" | "suspected" | "unscored"
    interest_reasoning: str = ""
    summary: str = ""
    transcript_excerpt_sha256: str | None = None
    recurrence_count: int = 1


class ReasonCount(BaseModel):
    reason_code: str
    count: int


class WeeklyReasonSeries(BaseModel):
    reason_code: str
    counts: list[int]


class WeeklyBreakdown(BaseModel):
    weeks: list[str]
    series: list[WeeklyReasonSeries]


class SubscriptionQuarantineStats(BaseModel):
    subscription_id: str
    quarantined: int
    total_items: int
    rate: float


class QuarantineStatsResponse(BaseModel):
    generated_at: str
    total_events: int
    verdict_counts: dict[str, int]
    reason_counts: list[ReasonCount]
    weekly: WeeklyBreakdown
    subscriptions: list[SubscriptionQuarantineStats]
    events: list[QuarantineEventDetail]


def _percentile_lookup(sorted_values: list[float]):
    """Returns a fn mapping a value to its percentile rank (0-100) within
    sorted_values via bisect, so no per-request O(n log n) sort is repeated."""
    n = len(sorted_values)

    def lookup(value: float | None) -> float | None:
        if value is None or n == 0:
            return None
        return round(bisect.bisect_right(sorted_values, value) / n * 100, 1)

    return lookup


def _verdict(interest_pct: float | None, engagement_pct: float | None) -> str:
    known = [p for p in (interest_pct, engagement_pct) if p is not None]
    if not known:
        return "unscored"
    liked = [p >= 50 for p in known]
    if all(liked):
        return "fooled"
    if not any(liked):
        return "suspected"
    return "split"


@router.get("/quarantine-stats/", response_model=QuarantineStatsResponse)
async def quarantine_stats():
    """Analytics over the quarantine history: how much is getting pulled, why,
    from where, and whether our own interest/engagement models liked it right
    before an external judge rejected it. Percentiles are computed against the
    NON-quarantined population so "above median" means "looked as good as a
    normal timeline item," not just "high relative to other junk."
    """
    es = get_es_client()

    async def _events() -> list[dict[str, Any]]:
        resp = await es.search(
            index=QUARANTINE_EVENTS_INDEX,
            body={
                "query": {"match_all": {}},
                "size": 5000,
                "sort": [{"created_at": {"order": "asc"}}],
            },
        )
        return [hit["_source"] for hit in resp["hits"]["hits"]]

    async def _population() -> tuple[list[float], list[float]]:
        # Non-quarantined items only, so a quarantined item's percentile means
        # "how it compares to a normal timeline item."
        resp = await es.search(
            index=CONTENT_ITEMS_INDEX,
            body={
                "query": {"bool": {"must_not": {"exists": {"field": "quarantined_at"}}}},
                "size": 10000,
                "_source": ["interest_score", "engagement.score"],
            },
        )
        hits = [hit["_source"] for hit in resp["hits"]["hits"]]
        interest = sorted(s["interest_score"] for s in hits if s.get("interest_score") is not None)
        engagement = sorted(
            (s.get("engagement") or {}).get("score")
            for s in hits
            if (s.get("engagement") or {}).get("score") is not None
        )
        return interest, engagement

    async def _totals_by_sub() -> dict[str, int]:
        resp = await es.search(
            index=CONTENT_ITEMS_INDEX,
            body={
                "query": {"match_all": {}},
                "size": 0,
                "aggs": {"by_sub": {"terms": {"field": "subscription_id", "size": 500}}},
            },
        )
        return {b["key"]: b["doc_count"] for b in resp["aggregations"]["by_sub"]["buckets"]}

    # None of these four queries depend on each other except the mget (needs
    # item_ids from the events query) — kick off events/population/totals
    # together up front so their Elastic Cloud round-trips overlap.
    events_task = asyncio.create_task(_events())
    population_task = asyncio.create_task(_population())
    totals_task = asyncio.create_task(_totals_by_sub())

    raw_events = await events_task
    generated_at = datetime.now(timezone.utc).isoformat()
    if not raw_events:
        population_task.cancel()
        totals_task.cancel()
        return QuarantineStatsResponse(
            generated_at=generated_at,
            total_events=0,
            verdict_counts={"fooled": 0, "split": 0, "suspected": 0, "unscored": 0},
            reason_counts=[],
            weekly=WeeklyBreakdown(weeks=[], series=[]),
            subscriptions=[],
            events=[],
        )

    item_ids = sorted({e["content_item_id"] for e in raw_events if e.get("content_item_id")})
    content_by_id: dict[str, dict[str, Any]] = {}
    if item_ids:
        mget_resp = await es.mget(index=CONTENT_ITEMS_INDEX, body={"ids": item_ids})
        content_by_id = {doc["_id"]: doc["_source"] for doc in mget_resp.get("docs", []) if doc.get("found")}

    (interest_pop, engagement_pop), total_items_by_sub = await asyncio.gather(population_task, totals_task)
    interest_percentile = _percentile_lookup(interest_pop)
    engagement_percentile = _percentile_lookup(engagement_pop)

    excerpt_counts = Counter(
        e["transcript_excerpt_sha256"] for e in raw_events if e.get("transcript_excerpt_sha256")
    )

    events: list[QuarantineEventDetail] = []
    verdict_counts = {"fooled": 0, "split": 0, "suspected": 0, "unscored": 0}
    reason_counter: Counter[str] = Counter()
    quarantined_by_sub: Counter[str] = Counter()
    weekly: dict[str, Counter[str]] = {}

    for e in raw_events:
        item = content_by_id.get(e.get("content_item_id", ""), {})
        i_score = item.get("interest_score")
        eng = item.get("engagement") or {}
        e_score = eng.get("score")
        i_pct = interest_percentile(i_score)
        e_pct = engagement_percentile(e_score)
        verdict = _verdict(i_pct, e_pct)
        verdict_counts[verdict] += 1
        reason_code = e.get("reason_code", "unknown")
        reason_counter[reason_code] += 1
        sub_id = item.get("subscription_id")
        if sub_id:
            quarantined_by_sub[sub_id] += 1

        created_at = e.get("created_at", generated_at)
        try:
            week_start = (
                datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                - timedelta(days=datetime.fromisoformat(created_at.replace("Z", "+00:00")).weekday())
            ).date().isoformat()
        except ValueError:
            week_start = created_at[:10]
        weekly.setdefault(week_start, Counter())[reason_code] += 1

        excerpt_hash = e.get("transcript_excerpt_sha256")
        events.append(
            QuarantineEventDetail(
                content_item_id=e.get("content_item_id", ""),
                external_id=e.get("external_id") or item.get("external_id"),
                title=item.get("title", ""),
                url=item.get("url", ""),
                type=item.get("type"),
                subscription_id=sub_id,
                created_at=created_at,
                discovered_at=item.get("discovered_at"),
                reason_code=reason_code,
                reason=e.get("reason", ""),
                source=e.get("source"),
                interest_score=i_score,
                interest_percentile=i_pct,
                engagement_score=e_score,
                engagement_percentile=e_pct,
                verdict=verdict,
                interest_reasoning=item.get("interest_reasoning", ""),
                summary=item.get("summary", ""),
                transcript_excerpt_sha256=excerpt_hash,
                recurrence_count=excerpt_counts.get(excerpt_hash, 1) if excerpt_hash else 1,
            )
        )

    weeks_sorted = sorted(weekly.keys())
    reason_codes_sorted = [rc for rc, _ in reason_counter.most_common()]
    weekly_series = [
        WeeklyReasonSeries(
            reason_code=rc,
            counts=[weekly[w].get(rc, 0) for w in weeks_sorted],
        )
        for rc in reason_codes_sorted
    ]

    subscriptions = [
        SubscriptionQuarantineStats(
            subscription_id=sub_id,
            quarantined=count,
            total_items=total_items_by_sub.get(sub_id, count),
            rate=round(count / total_items_by_sub[sub_id], 3) if total_items_by_sub.get(sub_id) else 1.0,
        )
        for sub_id, count in quarantined_by_sub.items()
    ]
    subscriptions.sort(key=lambda s: (s.rate, s.quarantined), reverse=True)

    return QuarantineStatsResponse(
        generated_at=generated_at,
        total_events=len(raw_events),
        verdict_counts=verdict_counts,
        reason_counts=[ReasonCount(reason_code=rc, count=c) for rc, c in reason_counter.most_common()],
        weekly=WeeklyBreakdown(weeks=weeks_sorted, series=weekly_series),
        subscriptions=subscriptions,
        events=events,
    )


@router.get("/{item_id}/", response_model=ContentItem)
async def get_content_item(item_id: str):
    es = get_es_client()
    try:
        resp = await es.get(index=CONTENT_ITEMS_INDEX, id=item_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Content item not found")
    return ContentItem(id=resp["_id"], **resp["_source"])


@router.post("/{item_id}/transcribe/")
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

    content_cache.invalidate()
    return {"status": "ok", "transcript_length": len(transcript.get("text", ""))}


# User-action writes use refresh="wait_for" so the change is searchable before
# the response returns. Without it, the immediately-following cache-repopulating
# read (or a client hard-refresh) can re-query ES before its ~1s refresh and
# cache the pre-write state for the full content_cache TTL — making a
# just-consumed item linger in unwatched lists.
@router.put("/{item_id}/consumed/")
async def set_consumed(item_id: str, consumed: bool = True):
    es = get_es_client()
    await es.update(
        index=CONTENT_ITEMS_INDEX, id=item_id, doc={"consumed": consumed}, refresh="wait_for"
    )
    content_cache.invalidate()
    return {"id": item_id, "consumed": consumed}


@router.put("/{item_id}/viewed/")
async def set_viewed(item_id: str):
    es = get_es_client()
    await es.update(
        index=CONTENT_ITEMS_INDEX, id=item_id, doc={"viewed": True}, refresh="wait_for"
    )
    content_cache.invalidate()
    return {"id": item_id, "viewed": True}


@router.put("/{item_id}/interest/")
async def set_interest(item_id: str, interest: str = "up"):
    """Set interest on a content item: 'up', 'down', or 'none' to clear."""
    es = get_es_client()
    if interest == "none":
        # Remove the field by setting to None via script
        await es.update(
            index=CONTENT_ITEMS_INDEX,
            id=item_id,
            script={"source": "ctx._source.remove('user_interest')"},
            refresh="wait_for",
        )
    else:
        await es.update(
            index=CONTENT_ITEMS_INDEX,
            id=item_id,
            doc={"user_interest": interest},
            refresh="wait_for",
        )
    content_cache.invalidate()
    return {"id": item_id, "interest": interest if interest != "none" else None}


@router.post("/playback-progress/")
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


@router.get("/export/csv/")
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


@router.delete("/{item_id}/")
async def delete_content_item(item_id: str):
    es = get_es_client()
    await es.delete(index=CONTENT_ITEMS_INDEX, id=item_id)
    content_cache.invalidate()
    return {"deleted": item_id}


@router.delete("/by-external-id/{external_id}/")
async def delete_by_external_id(external_id: str):
    """Delete a content item by its external_id (e.g. yt_dQw4w9WgXcQ)."""
    es = get_es_client()
    resp = await es.search(
        index=CONTENT_ITEMS_INDEX,
        body={
            "query": {"term": {"external_id": external_id}},
            "_source": False,
            "size": 1,
        },
    )
    hits = resp["hits"]["hits"]
    if not hits:
        raise HTTPException(
            status_code=404,
            detail=f"Content with external_id '{external_id}' not found",
        )
    item_id = hits[0]["_id"]
    await es.delete(index=CONTENT_ITEMS_INDEX, id=item_id)
    content_cache.invalidate()
    return {"status": "deleted", "external_id": external_id, "id": item_id}
