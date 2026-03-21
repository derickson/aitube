import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.app.models.subscription import Subscription, SubscriptionType
from backend.app.services import content_dlp
from backend.app.services.elasticsearch import (
    CONTENT_ITEMS_INDEX,
    SUBSCRIPTIONS_INDEX,
    get_es_client,
)

logger = logging.getLogger(__name__)

# Maps subscription type to content type stored in ES
SUB_TYPE_TO_CONTENT_TYPE = {
    SubscriptionType.youtube_channel: "video",
    SubscriptionType.podcast: "podcast_episode",
    SubscriptionType.rss: "article",
}


def _parse_dlp_item(
    raw: dict[str, Any],
    subscription: Subscription,
) -> dict[str, Any]:
    """Convert a content-dlp JSON object into an ES content_item document."""
    content_type = SUB_TYPE_TO_CONTENT_TYPE[subscription.type]

    published_at = None
    if raw.get("published_date"):
        try:
            published_at = raw["published_date"]
        except Exception:
            pass

    transcript = None
    if raw.get("transcript"):
        t = raw["transcript"]
        if isinstance(t, dict):
            transcript = t
        elif isinstance(t, str):
            transcript = {"text": t, "chunks": []}

    return {
        "subscription_id": subscription.id,
        "external_id": raw.get("content_id", raw.get("url", "")),
        "type": content_type,
        "title": raw.get("title", "Untitled"),
        "url": raw.get("url", ""),
        "published_at": published_at,
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": raw.get("duration_seconds"),
        "thumbnail_url": raw.get("thumbnail_url", ""),
        "summary": "",
        "interest_score": None,
        "interest_reasoning": "",
        "transcript": transcript,
        "content_markdown": raw.get("markdown", ""),
        "content_dlp_cache_id": raw.get("content_id", ""),
        "metadata": {
            "description": raw.get("description", ""),
            "author": raw.get("author"),
            "tags": raw.get("tags", []),
            "extras": raw.get("extras", {}),
        },
    }


async def _get_existing_external_ids(subscription_id: str) -> set[str]:
    """Return set of external_ids already stored for this subscription."""
    es = get_es_client()
    resp = await es.search(
        index=CONTENT_ITEMS_INDEX,
        body={
            "query": {"term": {"subscription_id": subscription_id}},
            "_source": ["external_id"],
            "size": 10000,
        },
    )
    return {hit["_source"]["external_id"] for hit in resp["hits"]["hits"]}


async def poll_subscription(subscription: Subscription) -> list[str]:
    """Poll a single subscription for new content. Returns list of new content item IDs."""
    logger.info("Polling %s: %s (%s)", subscription.type.value, subscription.name, subscription.url)

    try:
        if subscription.type == SubscriptionType.youtube_channel:
            raw = await content_dlp.fetch_youtube(subscription.url, no_audio=True)
            items_raw = [raw] if isinstance(raw, dict) else raw
        elif subscription.type == SubscriptionType.podcast:
            raw = await content_dlp.fetch_podcast(subscription.url, episodes=10, no_audio=True)
            items_raw = raw if isinstance(raw, list) else [raw]
        elif subscription.type == SubscriptionType.rss:
            raw = await content_dlp.fetch_webscrape(subscription.url)
            items_raw = [raw] if isinstance(raw, dict) else raw
        else:
            logger.warning("Unknown subscription type: %s", subscription.type)
            return []
    except Exception as e:
        logger.error("Failed to fetch content for %s: %s", subscription.name, e)
        return []

    existing_ids = await _get_existing_external_ids(subscription.id)
    es = get_es_client()
    new_ids = []

    for item_raw in items_raw:
        doc = _parse_dlp_item(item_raw, subscription)
        if doc["external_id"] in existing_ids:
            continue

        doc_id = str(uuid.uuid4())
        await es.index(index=CONTENT_ITEMS_INDEX, id=doc_id, document=doc)
        new_ids.append(doc_id)
        logger.info("New content: %s — %s", doc["title"], doc_id)

    # Update last_polled_at
    await es.update(
        index=SUBSCRIPTIONS_INDEX,
        id=subscription.id,
        doc={"last_polled_at": datetime.now(timezone.utc).isoformat()},
    )

    logger.info(
        "Finished polling %s: %d new item(s)", subscription.name, len(new_ids)
    )
    return new_ids


async def poll_all_active() -> dict[str, list[str]]:
    """Poll all active subscriptions. Returns {subscription_id: [new_content_ids]}."""
    es = get_es_client()
    resp = await es.search(
        index=SUBSCRIPTIONS_INDEX,
        body={
            "query": {"term": {"status": "active"}},
            "size": 1000,
        },
    )

    results: dict[str, list[str]] = {}
    for hit in resp["hits"]["hits"]:
        sub = Subscription(id=hit["_id"], **hit["_source"])
        new_ids = await poll_subscription(sub)
        if new_ids:
            results[sub.id] = new_ids

    return results
