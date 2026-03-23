import logging
import re
import uuid
import warnings
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from backend.app.models.subscription import Subscription, SubscriptionType
from backend.app.services import content_dlp
from backend.app.services.elasticsearch import (
    CONTENT_ITEMS_INDEX,
    PLAYBACK_STATE_INDEX,
    SUBSCRIPTIONS_INDEX,
    get_es_client,
)

from dateutil import parser as dateparser

logger = logging.getLogger(__name__)

_RSS_DATE_FORMATS = [
    "%a, %d %b %Y %H:%M:%S %z",
    "%a, %d %b %Y %H:%M:%S %Z",
    "%d %b %Y %H:%M:%S %z",
]


def _normalize_date_to_iso(value: str) -> str | None:
    """Parse a date string from any common feed format into ISO 8601."""
    if not value or not value.strip():
        return None

    value = value.strip()

    # Already ISO 8601 — pass through
    if re.match(r"^\d{4}-\d{2}-\d{2}(T|\s)", value):
        return value

    # Try dateutil (handles RFC 2822 and most formats)
    try:
        dt = dateparser.parse(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except (ValueError, OverflowError):
        pass

    # Try common RSS date formats explicitly
    for fmt in _RSS_DATE_FORMATS:
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            continue

    logger.warning("Could not parse date: %s", value)
    return None


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
        published_at = _normalize_date_to_iso(raw["published_date"])

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

    # Skip unreleased premieres/scheduled videos (0 views = not yet available)
    stats_tag = entry.find("media:statistics") or entry.find("statistics")
    if stats_tag and stats_tag.get("views") == "0":
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

    return items


def _parse_rss_feed_entry(item: Any, feed_url: str) -> dict[str, Any]:
    """Convert an RSS/Atom feed entry into a content-dlp-like dict."""
    title_tag = item.find("title")
    title = title_tag.get_text(strip=True) if title_tag else "Untitled"

    # RSS: <link> text, Atom: <link href="">
    # Note: BeautifulSoup's HTML parser treats <link> as void/self-closing,
    # so text content is lost. Fall back to <guid> which often contains the URL.
    link_tag = item.find("link")
    url = ""
    if link_tag:
        url = link_tag.get("href", "") or link_tag.get_text(strip=True)

    # GUID as external ID, fallback to URL
    guid_tag = item.find("guid") or item.find("id")
    guid = guid_tag.get_text(strip=True) if guid_tag else url

    # If link parsing failed, use guid as URL if it looks like one
    if not url and guid and guid.startswith("http"):
        url = guid
    # Create a stable short ID
    import hashlib
    external_id = f"rss_{hashlib.md5(guid.encode()).hexdigest()[:12]}"

    pub_tag = item.find("pubdate") or item.find("pubDate") or item.find("published") or item.find("updated")
    published = pub_tag.get_text(strip=True) if pub_tag else None

    desc_tag = item.find("description") or item.find("summary") or item.find("content")
    description = desc_tag.get_text(strip=True) if desc_tag else ""

    # Extract inline <img src="..."> from description text (HTML-escaped in RSS becomes literal text)
    desc_img = ""
    img_match = re.search(r'<img[^>]+src=["\']?(https?://[^\s"\'>\)]+)', description)
    if img_match:
        desc_img = img_match.group(1)
        # Clean the img tag text out of the plain-text description
        description = re.sub(r'<img[^>]*>', '', description).strip()

    # Look for images — try media:thumbnail, media:content, enclosure, then description img
    thumbnail = ""
    media_thumb = item.find("media:thumbnail") or item.find("thumbnail")
    if media_thumb:
        thumbnail = media_thumb.get("url", "")
    if not thumbnail:
        media_content = item.find("media:content") or item.find("content", attrs={"medium": "image"})
        if media_content and media_content.get("url"):
            thumbnail = media_content.get("url", "")
    if not thumbnail:
        enclosure = item.find("enclosure")
        if enclosure and "image" in (enclosure.get("type") or ""):
            thumbnail = enclosure.get("url", "")
    if not thumbnail and desc_img:
        thumbnail = desc_img

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
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    filtered = []
    for item in items:
        pub = item.get("published_date")
        if pub:
            iso = _normalize_date_to_iso(pub)
            if iso:
                try:
                    pub_dt = dateparser.parse(iso)
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

            # Clean up scraped markdown (remove nav/footer garbage)
            if doc.get("content_markdown"):
                from backend.app.services.content_cleanup import cleanup_article_markdown
                try:
                    result = await cleanup_article_markdown(doc["content_markdown"], doc["title"])
                    doc["content_markdown"] = result["markdown"]
                    # Use extracted image as thumbnail if feed didn't provide one
                    if not doc.get("thumbnail_url") and result.get("image_url"):
                        doc["thumbnail_url"] = result["image_url"]
                except Exception as e:
                    logger.warning("Failed to clean up article %s: %s", doc["title"], e)

        # For YouTube videos, fetch metadata via yt-dlp to check for livestreams and get captions
        if subscription.type == SubscriptionType.youtube_channel and doc["url"]:
            from backend.app.services.youtube_captions import fetch_video_metadata
            try:
                meta = fetch_video_metadata(doc["url"])
                if meta and meta["is_live"]:
                    logger.info("Skipping livestream: %s", doc["title"])
                    continue
                if meta and meta.get("duration"):
                    doc["duration_seconds"] = meta["duration"]
                if meta and meta["captions"]:
                    doc["transcript"] = meta["captions"]
            except Exception as e:
                logger.warning("Failed to fetch metadata for %s: %s", doc["url"], e)

        if subscription.type == SubscriptionType.youtube_channel:
            transcript = doc.get("transcript")
            if not transcript and doc["url"]:
                try:
                    logger.info("No captions available, falling back to content-dlp transcription for %s", doc["url"])
                    yt_data = await content_dlp.fetch_youtube(doc["url"], no_audio=False, transcript=True)
                    if yt_data.get("transcript"):
                        t = yt_data["transcript"]
                        doc["transcript"] = t if isinstance(t, dict) else {"text": t, "chunks": []}
                except Exception as e:
                    logger.warning("content-dlp transcription also failed for %s: %s", doc["url"], e)

            # Fallback: derive duration from transcript chunks if still missing
            if not doc.get("duration_seconds"):
                t = doc.get("transcript")
                if isinstance(t, dict) and t.get("chunks"):
                    last_end = t["chunks"][-1].get("end", 0)
                    if last_end > 0:
                        doc["duration_seconds"] = last_end

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

                    # Ad detection disabled — not working well enough yet
                    # transcript_obj = doc["transcript"]
                    # if isinstance(transcript_obj, dict) and transcript_obj.get("chunks"):
                    #     from backend.app.services.ad_detector import detect_ad_end
                    #     ad_skip_to = await detect_ad_end(transcript_obj["chunks"])
            except Exception as e:
                logger.warning("Failed to transcribe podcast %s: %s", doc["title"], e)

        # Generate AI summary
        transcript_obj = doc.get("transcript")
        transcript_text = ""
        transcript_chunks = None
        if isinstance(transcript_obj, dict):
            transcript_text = transcript_obj.get("text", "")
            if transcript_obj.get("chunks"):
                transcript_chunks = transcript_obj["chunks"]
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
                    transcript_chunks=transcript_chunks,
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


async def deduplicate_recent_items(days: int = 7) -> int:
    """Find and remove duplicate content items from the last N days.

    Groups items by URL; for each group with >1 item, keeps the one
    with the most user interaction and deletes the rest.
    Returns the number of items removed.
    """
    es = get_es_client()
    resp = await es.search(
        index=CONTENT_ITEMS_INDEX,
        body={
            "query": {
                "range": {"discovered_at": {"gte": f"now-{days}d"}},
            },
            "_source": ["url", "title", "consumed", "user_interest", "discovered_at"],
            "size": 5000,
        },
    )

    hits = resp["hits"]["hits"]
    if not hits:
        return 0

    # Group by URL
    url_groups: dict[str, list[dict]] = defaultdict(list)
    for hit in hits:
        url = hit["_source"].get("url", "")
        if url:
            url_groups[url].append(hit)

    # Find groups with duplicates
    to_delete: list[str] = []
    for url, group in url_groups.items():
        if len(group) <= 1:
            continue

        # Score each item: consumed(+4), user_interest set(+2), earlier discovered_at as tiebreak
        def score(hit: dict) -> tuple:
            src = hit["_source"]
            consumed = 1 if src.get("consumed") else 0
            has_interest = 1 if src.get("user_interest") else 0
            # Earlier discovered_at wins (negate for sort: earlier = larger negative = sorted first)
            discovered = src.get("discovered_at", "9999")
            return (consumed, has_interest, discovered)

        # Sort: highest score first, earliest discovered_at as tiebreak (ascending discovered = keep oldest)
        group.sort(key=lambda h: (-score(h)[0], -score(h)[1], score(h)[2]))
        keeper = group[0]
        duplicates = group[1:]

        for dup in duplicates:
            to_delete.append(dup["_id"])
            logger.info(
                "Dedup: removing '%s' (%s) — keeping %s",
                dup["_source"].get("title", "?")[:60],
                dup["_id"],
                keeper["_id"],
            )

    if not to_delete:
        return 0

    # Delete duplicate content items and their playback states
    for item_id in to_delete:
        try:
            await es.delete(index=CONTENT_ITEMS_INDEX, id=item_id)
        except Exception as e:
            logger.warning("Failed to delete content item %s: %s", item_id, e)
        try:
            # Delete playback state by content_item_id (uses delete_by_query)
            await es.delete_by_query(
                index=PLAYBACK_STATE_INDEX,
                body={"query": {"term": {"content_item_id": item_id}}},
            )
        except Exception:
            pass  # No playback state is fine

    logger.info("Removed %d duplicate(s) in post-poll cleanup", len(to_delete))
    return len(to_delete)


async def backfill_missing_transcripts(limit: int = 5) -> int:
    """Retry caption fetch for recent YouTube videos missing transcripts.

    Only uses yt-dlp captions (not content-dlp transcription) to stay lightweight.
    Processes at most `limit` videos per cycle to avoid YouTube rate limits.
    Returns the number of videos successfully backfilled.
    """
    from backend.app.services.youtube_captions import fetch_video_metadata
    from backend.app.services.summarizer import summarize_content

    es = get_es_client()
    # transcript field is "enabled: false" so we can't filter on it in ES;
    # fetch recent videos and filter in Python
    resp = await es.search(
        index=CONTENT_ITEMS_INDEX,
        body={
            "query": {
                "bool": {
                    "must": [
                        {"term": {"type": "video"}},
                        {"range": {"discovered_at": {"gte": "now-14d"}}},
                    ],
                }
            },
            "_source": ["title", "url", "type", "transcript", "metadata", "summary"],
            "size": 200,
        },
    )

    # Filter to videos with no transcript
    candidates = []
    for hit in resp["hits"]["hits"]:
        src = hit["_source"]
        transcript = src.get("transcript")
        has_transcript = (
            isinstance(transcript, dict)
            and transcript.get("text")
        )
        if not has_transcript:
            candidates.append(hit)

    if not candidates:
        return 0

    backfilled = 0
    for hit in candidates[:limit]:
        doc_id = hit["_id"]
        src = hit["_source"]
        title = src.get("title", "?")
        url = src.get("url", "")
        if not url:
            continue

        try:
            transcript = None

            # Try yt-dlp captions first
            meta = fetch_video_metadata(url)
            if meta and meta.get("captions"):
                transcript = meta["captions"]

            # Fall back to content-dlp transcription (handles 429 rate limits on yt-dlp)
            if not transcript:
                try:
                    yt_data = await content_dlp.fetch_youtube(url, no_audio=False, transcript=True)
                    if yt_data.get("transcript"):
                        t = yt_data["transcript"]
                        transcript = t if isinstance(t, dict) else {"text": t, "chunks": []}
                except Exception as e:
                    logger.warning("Backfill: content-dlp also failed for '%s': %s", title[:60], e)

            if not transcript:
                continue
            update_fields: dict[str, Any] = {"transcript": transcript}

            # Re-generate summary with transcript timestamps
            transcript_text = transcript.get("text", "") if isinstance(transcript, dict) else ""
            transcript_chunks = transcript.get("chunks") if isinstance(transcript, dict) else None
            description = src.get("metadata", {}).get("description", "")
            author = src.get("metadata", {}).get("author", "")

            new_summary = await summarize_content(
                title=title,
                content_type="video",
                transcript_text=transcript_text,
                description=description,
                author=author,
                transcript_chunks=transcript_chunks,
            )
            if new_summary:
                update_fields["summary"] = new_summary

            # Also backfill duration if missing
            if not hit["_source"].get("duration_seconds") and meta.get("duration"):
                update_fields["duration_seconds"] = meta["duration"]

            await es.update(
                index=CONTENT_ITEMS_INDEX,
                id=doc_id,
                body={"doc": update_fields},
            )
            backfilled += 1
            logger.info("Backfill: fetched transcript for '%s' (%s)", title[:60], doc_id)

        except Exception as e:
            logger.warning("Backfill: failed for '%s' (%s): %s", title[:60], doc_id, e)

    if backfilled:
        logger.info("Backfilled transcripts for %d video(s)", backfilled)
    return backfilled


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

    # Deduplicate subscriptions by URL to avoid polling same feed multiple times
    seen_urls: dict[str, str] = {}
    unique_subs: list[Subscription] = []
    for hit in resp["hits"]["hits"]:
        sub = Subscription(id=hit["_id"], **hit["_source"])
        if sub.url in seen_urls:
            logger.warning(
                "Skipping duplicate subscription %s (%s) — same URL as %s",
                sub.name, sub.id, seen_urls[sub.url],
            )
            continue
        seen_urls[sub.url] = sub.id
        unique_subs.append(sub)

    results: dict[str, list[str]] = {}
    for sub in unique_subs:
        new_ids = await poll_subscription(sub)
        if new_ids:
            results[sub.id] = new_ids

    # Clean up any duplicate content items from recent polls
    await deduplicate_recent_items()

    # Retry transcript fetch for videos that failed on initial poll
    await backfill_missing_transcripts()

    return results
