import logging

from fastapi import APIRouter

from backend.app.services.feed_poller import poll_all_active, poll_subscription
from backend.app.models.subscription import Subscription
from backend.app.services import content_cache
from backend.app.services.elasticsearch import SUBSCRIPTIONS_INDEX, get_es_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/polling", tags=["polling"])


@router.post("/trigger/")
async def trigger_poll():
    """Poll all active subscriptions for new content."""
    results = await poll_all_active()
    total_new = sum(len(ids) for ids in results.values())
    return {
        "status": "ok",
        "subscriptions_polled": len(results),
        "new_items": total_new,
        "details": {sub_id: len(ids) for sub_id, ids in results.items()},
    }


@router.post("/trigger/{sub_id}/")
async def trigger_poll_single(sub_id: str):
    """Poll a single subscription for new content."""
    es = get_es_client()
    resp = await es.get(index=SUBSCRIPTIONS_INDEX, id=sub_id)
    sub = Subscription(id=resp["_id"], **resp["_source"])
    new_ids = await poll_subscription(sub)
    if new_ids:
        content_cache.invalidate()
    return {
        "status": "ok",
        "subscription": sub.name,
        "new_items": len(new_ids),
        "new_item_ids": new_ids,
    }
