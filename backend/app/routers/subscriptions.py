import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.app.models.subscription import (
    Subscription,
    SubscriptionCreate,
    SubscriptionUpdate,
)
from backend.app.services.elasticsearch import (
    CONTENT_ITEMS_INDEX,
    SUBSCRIPTIONS_INDEX,
    get_es_client,
)
from backend.app.services.url_resolver import resolve_url

router = APIRouter(prefix="/api/subscriptions", tags=["subscriptions"])


class ResolveRequest(BaseModel):
    url: str


class ResolvedPreview(BaseModel):
    url: str
    feed_url: str
    type: str
    name: str
    description: str = ""
    thumbnail_url: str = ""
    sample_items: list[dict] = []


@router.post("/resolve/", response_model=ResolvedPreview)
async def resolve_subscription_url(data: ResolveRequest):
    """Resolve a raw URL into subscription metadata for preview."""
    try:
        result = await resolve_url(data.url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not resolve URL: {e}")
    return ResolvedPreview(
        url=result.url,
        feed_url=result.feed_url,
        type=result.type,
        name=result.name,
        description=result.description,
        thumbnail_url=result.thumbnail_url,
        sample_items=result.sample_items,
    )


@router.post("/", response_model=Subscription)
async def create_subscription(data: SubscriptionCreate):
    es = get_es_client()
    doc_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    doc = {
        **data.model_dump(),
        "status": "active",
        "added_at": now.isoformat(),
        "last_polled_at": None,
    }
    await es.index(index=SUBSCRIPTIONS_INDEX, id=doc_id, document=doc)
    return Subscription(id=doc_id, **doc)


class SubscriptionWithCount(Subscription):
    content_count: int = 0


@router.get("/", response_model=list[SubscriptionWithCount])
async def list_subscriptions():
    es = get_es_client()

    # Fetch subscriptions and content counts in parallel
    sub_resp, count_resp = await asyncio.gather(
        es.search(
            index=SUBSCRIPTIONS_INDEX,
            body={"query": {"match_all": {}}, "size": 1000, "sort": [{"added_at": "desc"}]},
        ),
        es.search(
            index=CONTENT_ITEMS_INDEX,
            body={
                "size": 0,
                "aggs": {"per_sub": {"terms": {"field": "subscription_id", "size": 10000}}},
            },
        ),
    )

    # Build count map from aggregation
    count_map: dict[str, int] = {}
    for bucket in count_resp.get("aggregations", {}).get("per_sub", {}).get("buckets", []):
        count_map[bucket["key"]] = bucket["doc_count"]

    results = []
    for hit in sub_resp["hits"]["hits"]:
        sub = SubscriptionWithCount(
            id=hit["_id"],
            **hit["_source"],
            content_count=count_map.get(hit["_id"], 0),
        )
        results.append(sub)
    return results


@router.get("/{sub_id}/", response_model=Subscription)
async def get_subscription(sub_id: str):
    es = get_es_client()
    try:
        resp = await es.get(index=SUBSCRIPTIONS_INDEX, id=sub_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return Subscription(id=resp["_id"], **resp["_source"])


@router.patch("/{sub_id}/", response_model=Subscription)
async def update_subscription(sub_id: str, data: SubscriptionUpdate):
    es = get_es_client()
    updates = data.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    await es.update(index=SUBSCRIPTIONS_INDEX, id=sub_id, doc=updates)
    resp = await es.get(index=SUBSCRIPTIONS_INDEX, id=sub_id)
    return Subscription(id=resp["_id"], **resp["_source"])


@router.delete("/{sub_id}/")
async def delete_subscription(sub_id: str):
    es = get_es_client()
    await es.delete(index=SUBSCRIPTIONS_INDEX, id=sub_id)
    return {"deleted": sub_id}
