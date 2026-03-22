import logging
import re
import uuid
import warnings
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

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


def _parse_youtube_feed_entry(entry: Any) -> dict[str, Any] | None:
    """Convert a YouTube Atom feed <entry> into a content-dlp-like dict.
    Returns None if the entry is a YouTube Short."""
    video_id_tag = entry.find("yt:videoid") or entry.find("videoid")
    video_id = video_id_tag.get_text(strip=True) if video_id_tag else ""

    # Check if this is a Short via the link href
    link_tag = entry.find("link", rel="alternate")
    link_href = link_tag.get("href", "") if link_tag else ""
    if "/shorts/" in link_href:
        return None

    title_tag = entry.find("title")
    title = title_tag.get_text(strip=True) if title_tag else "Untitled"

    published_tag = entry.find("published")
    published = published_tag.get_text(strip=True) if published_tag else None

    thumbnail = entry.find("media:thumbnail") or entry.find("thumbnail")
    thumb_url = thumbnail.get("url", "") if thumbnail else ""
    if not thumb_url and video_id:
        thumb_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

    desc_tag = entry.find("media:description") or entry.find("description")
    description = desc_tag.get_text(strip=True) if desc_tag else ""

    author_tag = entry.find("author")
    author = ""
    if author_tag:
        name_tag = author_tag.find("name")
        if name_tag:
            author = name_tag.get_text(strip=True)

    return {
        "content_id": f"yt_{video_id}",
        "source_type": "youtube",
        "url": f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
        "title": title,
        "description": description,
        "author": author,
        "published_date": published,
        "duration_seconds": None,
        "tags": [],
        "thumbnail_url": thumb_url,
    }


async def _resolve_youtube_channel_id(channel_url: str) -> str | None:
    """Extract channel ID from a YouTube channel page."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(channel_url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
        # Look for channel ID in meta tags or page source
        match = re.search(r'"externalId"\s*:\s*"(UC[\w-]+)"', resp.text)
        if match:
            return match.group(1)
        # Try meta tag
        match = re.search(r'<meta\s+itemprop="channelId"\s+content="(UC[\w-]+)"', resp.text)
        if match:
            return match.group(1)
        # Try RSS link in page
        match = re.search(r'channel_id=(UC[\w-]+)', resp.text)
        if match:
            return match.group(1)
    except Exception as e:
        logger.warning("Failed to resolve YouTube channel ID from %s: %s", channel_url, e)
    return None


async def _fetch_youtube_channel_feed(channel_url: str) -> list[dict[str, Any]]:
    """Fetch recent videos from a YouTube channel via its Atom feed, filtered by max age."""
    from backend.app.config import settings

    channel_id = await _resolve_youtube_channel_id(channel_url)
    if not channel_id:
        logger.warning("Could not resolve channel ID for %s", channel_url)
        return []

    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        resp = await client.get(feed_url, headers={"User-Agent": "AITube/0.1"})
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    entries = soup.find_all("entry")

    items = []
    for entry in entries[:15]:
        parsed = _parse_youtube_feed_entry(entry)
        if parsed is not None:
            items.append(parsed)

    items = _filter_by_age(items, settings.youtube_max_age_days)

    # Fetch captions for each video via yt-dlp (one call per video)
    if items:
        from backend.app.services.youtube_captions import fetch_captions
        for item in items:
            try:
                transcript = fetch_captions(item["url"])
                if transcript:
                    item["_transcript"] = transcript
            except Exception as e:
                logger.warning("Failed to fetch captions for %s: %s", item["url"], e)

    return items


def _parse_rss_feed_entry(item: Any, feed_url: str) -> dict[str, Any]:
    """Convert an RSS/Atom feed entry into a content-dlp-like dict."""
    title_tag = item.find("title")
    title = title_tag.get_text(strip=True) if title_tag else "Untitled"

    # RSS: <link> text, Atom: <link href="">
    link_tag = item.find("link")
    url = ""
    if link_tag:
        url = link_tag.get("href", "") or link_tag.get_text(strip=True)

    # GUID as external ID, fallback to URL
    guid_tag = item.find("guid") or item.find("id")
    guid = guid_tag.get_text(strip=True) if guid_tag else url
    # Create a stable short ID
    import hashlib
    external_id = f"rss_{hashlib.md5(guid.encode()).hexdigest()[:12]}"

    pub_tag = item.find("pubdate") or item.find("pubDate") or item.find("published") or item.find("updated")
    published = pub_tag.get_text(strip=True) if pub_tag else None

    desc_tag = item.find("description") or item.find("summary") or item.find("content")
    description = desc_tag.get_text(strip=True) if desc_tag else ""

    # Look for images
    thumbnail = ""
    media_thumb = item.find("media:thumbnail") or item.find("thumbnail")
    if media_thumb:
        thumbnail = media_thumb.get("url", "")
    if not thumbnail:
        enclosure = item.find("enclosure")
        if enclosure and "image" in (enclosure.get("type") or ""):
            thumbnail = enclosure.get("url", "")

    return {
        "content_id": external_id,
        "source_type": "rss",
        "url": url,
        "title": title,
        "description": description,
        "author": None,
        "published_date": published,
        "duration_seconds": None,
        "tags": [],
        "thumbnail_url": thumbnail or None,
    }


async def _fetch_rss_feed(feed_url: str) -> list[dict[str, Any]]:
    """Fetch and parse an RSS/Atom feed, returning individual items."""
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        resp = await client.get(feed_url, headers={"User-Agent": "AITube/0.1"})
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    items = []

    # RSS items
    for entry in soup.find_all("item")[:30]:
        items.append(_parse_rss_feed_entry(entry, feed_url))

    # Atom entries (if no RSS items found)
    if not items:
        for entry in soup.find_all("entry")[:30]:
            items.append(_parse_rss_feed_entry(entry, feed_url))

    return items


def _filter_by_age(items: list[dict[str, Any]], max_age_days: int) -> list[dict[str, Any]]:
    """Filter items to only those published within max_age_days."""
    from dateutil import parser as dateparser

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    filtered = []
    for item in items:
        pub = item.get("published_date")
        if pub:
            try:
                pub_dt = dateparser.parse(pub)
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue
            except Exception:
                pass
        filtered.append(item)
    return filtered


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
            items_raw = await _fetch_youtube_channel_feed(subscription.url)
        elif subscription.type == SubscriptionType.podcast:
            from backend.app.config import settings
            raw = await content_dlp.fetch_podcast(subscription.url, episodes=10, no_audio=True)
            items_raw = raw if isinstance(raw, list) else [raw]
            items_raw = _filter_by_age(items_raw, settings.podcast_max_age_days)
        elif subscription.type == SubscriptionType.rss:
            from backend.app.config import settings
            items_raw = await _fetch_rss_feed(subscription.url)
            items_raw = _filter_by_age(items_raw, settings.rss_max_age_days)
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

        # For RSS articles, scrape the full page content via content-dlp
        if subscription.type == SubscriptionType.rss and doc["url"]:
            try:
                scraped = await content_dlp.fetch_webscrape(doc["url"])
                doc["content_markdown"] = scraped.get("markdown", "")
                doc["content_dlp_cache_id"] = scraped.get("content_id", "")
            except Exception as e:
                logger.warning("Failed to scrape %s: %s", doc["url"], e)

        # For YouTube videos, use captions from feed parsing, fall back to content-dlp
        if subscription.type == SubscriptionType.youtube_channel:
            transcript = item_raw.get("_transcript")
            if transcript:
                doc["transcript"] = transcript
            elif doc["url"]:
                try:
                    logger.info("No captions available, falling back to content-dlp transcription for %s", doc["url"])
                    yt_data = await content_dlp.fetch_youtube(doc["url"], no_audio=False, transcript=True)
                    if yt_data.get("transcript"):
                        t = yt_data["transcript"]
                        doc["transcript"] = t if isinstance(t, dict) else {"text": t, "chunks": []}
                except Exception as e:
                    logger.warning("content-dlp transcription also failed for %s: %s", doc["url"], e)

        # For podcast episodes, download audio and transcribe
        ad_skip_to: float | None = None
        if subscription.type == SubscriptionType.podcast and doc["url"]:
            try:
                extras = doc.get("metadata", {}).get("extras", {})
                audio_url = extras.get("enclosure_url", doc["url"])
                logger.info("Downloading and transcribing podcast: %s", doc["title"])
                transcript_data = await content_dlp.download_and_transcribe(audio_url)
                if transcript_data.get("transcript"):
                    t = transcript_data["transcript"]
                    doc["transcript"] = t if isinstance(t, dict) else {"text": t, "chunks": []}

                    # Detect ads at the start and find where content begins
                    transcript_obj = doc["transcript"]
                    if isinstance(transcript_obj, dict) and transcript_obj.get("chunks"):
                        from backend.app.services.ad_detector import detect_ad_end
                        ad_skip_to = await detect_ad_end(transcript_obj["chunks"])
            except Exception as e:
                logger.warning("Failed to transcribe podcast %s: %s", doc["title"], e)

        # Generate AI summary
        transcript_obj = doc.get("transcript")
        transcript_text = ""
        if isinstance(transcript_obj, dict):
            transcript_text = transcript_obj.get("text", "")
        # For articles, use the markdown content instead of transcript
        source_text = transcript_text or doc.get("content_markdown", "")
        if source_text or doc.get("metadata", {}).get("description"):
            try:
                from backend.app.services.summarizer import summarize_content
                summary = await summarize_content(
                    title=doc["title"],
                    content_type=doc["type"],
                    transcript_text=source_text,
                    description=doc.get("metadata", {}).get("description", ""),
                    author=doc.get("metadata", {}).get("author", ""),
                )
                if summary:
                    doc["summary"] = summary
            except Exception as e:
                logger.warning("Failed to summarize %s: %s", doc["title"], e)

        doc_id = str(uuid.uuid4())
        await es.index(index=CONTENT_ITEMS_INDEX, id=doc_id, document=doc)
        new_ids.append(doc_id)
        logger.info("New content: %s — %s", doc["title"], doc_id)

        # If an ad was detected, set the playback position to skip past it
        if ad_skip_to is not None and ad_skip_to > 0:
            from backend.app.services.elasticsearch import PLAYBACK_STATE_INDEX
            await es.index(
                index=PLAYBACK_STATE_INDEX,
                document={
                    "content_item_id": doc_id,
                    "position_seconds": round(ad_skip_to / 5) * 5,
                    "consumed": False,
                    "last_updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            logger.info("Set playback to %.0fs to skip ad for %s", ad_skip_to, doc["title"])

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
