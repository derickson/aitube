"""
Ingest individual content items by URL.

Supports YouTube videos, direct audio files, and web articles.
Automatically creates a muted source subscription for grouping.
"""

import hashlib
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from backend.app.services import content_dlp
from backend.app.services.elasticsearch import (
    CONTENT_ITEMS_INDEX,
    SUBSCRIPTIONS_INDEX,
    get_es_client,
)
from backend.app.services.feed_poller import _normalize_date_to_iso

logger = logging.getLogger(__name__)

YOUTUBE_VIDEO_PATTERN = r"(?:youtube\.com/watch\?.*v=|youtu\.be/)[\w-]+"
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".ogg", ".wav", ".flac", ".aac", ".opus", ".mp4"}


def detect_url_type(url: str) -> str:
    """Returns 'youtube_video', 'audio', or 'article'."""
    if re.search(YOUTUBE_VIDEO_PATTERN, url):
        return "youtube_video"
    parsed = urlparse(url)
    _, ext = os.path.splitext(parsed.path.lower())
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    return "article"


async def _fetch_html_metadata(url: str) -> dict:
    """Fetch HTML and extract Open Graph / meta tags for quick preview."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
    except Exception as e:
        logger.warning("Failed to fetch URL %s: %s", url, e)
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")

    def meta_content(name=None, property=None) -> str:
        if name:
            tag = soup.find("meta", attrs={"name": name})
        else:
            tag = soup.find("meta", property=property)
        return (tag.get("content") or "") if tag else ""

    title = (
        meta_content(property="og:title")
        or meta_content(name="twitter:title")
        or (soup.title.get_text(strip=True) if soup.title else "")
    )
    description = meta_content(property="og:description") or meta_content(name="description")
    thumbnail_url = meta_content(property="og:image") or meta_content(name="twitter:image")
    published_at = (
        meta_content(property="article:published_time")
        or meta_content(name="article:published_time")
        or meta_content(property="article:published")
    )
    author = meta_content(name="author") or meta_content(property="article:author")

    return {
        "title": title.strip(),
        "description": description.strip(),
        "thumbnail_url": thumbnail_url.strip(),
        "published_at": published_at.strip(),
        "author": author.strip(),
    }


async def preview_content_url(url: str) -> dict:
    """Fetch metadata for a URL for preview display (no full ingestion)."""
    url_type = detect_url_type(url)

    if url_type == "youtube_video":
        try:
            data = await content_dlp.fetch_youtube(url, no_audio=True, transcript=False)
            extras = data.get("extras", {})
            channel_name = data.get("author", "Unknown Channel")
            channel_url = extras.get("channel_url", "")
            return {
                "type": "video",
                "url": url,
                "title": data.get("title", ""),
                "description": data.get("description", ""),
                "thumbnail_url": data.get("thumbnail_url", ""),
                "published_at": data.get("published_date"),
                "source_name": channel_name,
                "source_url": channel_url,
                "duration_seconds": data.get("duration_seconds"),
                "author": channel_name,
            }
        except Exception as e:
            raise ValueError(f"Failed to fetch YouTube metadata: {e}") from e

    elif url_type == "audio":
        parsed = urlparse(url)
        filename = parsed.path.split("/")[-1]
        domain = parsed.netloc
        return {
            "type": "podcast_episode",
            "url": url,
            "title": filename,
            "description": "",
            "thumbnail_url": "",
            "published_at": None,
            "source_name": domain,
            "source_url": f"{parsed.scheme}://{domain}",
            "duration_seconds": None,
            "author": "",
        }

    else:  # article
        html_meta = await _fetch_html_metadata(url)
        parsed = urlparse(url)
        domain = parsed.netloc
        source_name = _build_article_source_name(domain, html_meta.get("author", ""))
        return {
            "type": "article",
            "url": url,
            "title": html_meta.get("title") or url,
            "description": html_meta.get("description", ""),
            "thumbnail_url": html_meta.get("thumbnail_url", ""),
            "published_at": html_meta.get("published_at") or None,
            "source_name": source_name,
            "source_url": f"{parsed.scheme}://{domain}",
            "duration_seconds": None,
            "author": html_meta.get("author", ""),
        }


def _build_article_source_name(domain: str, author: str) -> str:
    """Build a human-readable source name from domain and optional author."""
    # Substack and similar: prefer "Author (Substack)"
    if "substack.com" in domain and author:
        return f"{author} (Substack)"
    if "medium.com" in domain and author:
        return f"{author} (Medium)"
    return domain


async def _get_or_create_source_subscription(
    subscription_type: str,
    url: str,
    name: str,
    description: str = "",
    thumbnail_url: str = "",
) -> str:
    """Find existing subscription for URL or create a muted one. Returns subscription_id."""
    es = get_es_client()

    # Normalise the URL for lookup (strip trailing slash)
    lookup_url = url.rstrip("/")

    try:
        resp = await es.search(
            index=SUBSCRIPTIONS_INDEX,
            body={"query": {"term": {"url": lookup_url}}, "size": 1},
        )
        hits = resp["hits"]["hits"]
        if hits:
            return hits[0]["_id"]
    except Exception as e:
        logger.warning("Subscription lookup failed: %s", e)

    # Create a muted subscription so content is grouped but not auto-polled
    doc_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    doc = {
        "type": subscription_type,
        "url": lookup_url,
        "name": name,
        "description": description,
        "interest_notes": "",
        "status": "muted",
        "added_at": now.isoformat(),
        "last_polled_at": None,
    }
    await es.index(index=SUBSCRIPTIONS_INDEX, id=doc_id, document=doc)
    logger.info("Created ad-hoc muted subscription '%s' (%s)", name, doc_id)
    return doc_id


async def _check_already_ingested(external_id: str) -> str | None:
    """Return existing content item id if external_id already indexed, else None."""
    es = get_es_client()
    try:
        resp = await es.search(
            index=CONTENT_ITEMS_INDEX,
            body={"query": {"term": {"external_id": external_id}}, "size": 1},
        )
        hits = resp["hits"]["hits"]
        if hits:
            return hits[0]["_id"]
    except Exception:
        pass
    return None


async def ingest_content_url(url: str) -> str:
    """
    Run the full ingestion pipeline for a single URL.

    Returns the new (or existing) content item id.
    Supports YouTube videos, direct audio, and web articles.
    """
    url_type = detect_url_type(url)

    if url_type == "youtube_video":
        return await _ingest_youtube(url)
    elif url_type == "audio":
        return await _ingest_audio(url)
    else:
        return await _ingest_article(url)


async def _ingest_youtube(url: str) -> str:
    video_id_match = re.search(r"(?:v=|youtu\.be/)([\w-]+)", url)
    video_id = video_id_match.group(1) if video_id_match else hashlib.md5(url.encode()).hexdigest()[:12]
    external_id = f"yt_{video_id}"

    existing = await _check_already_ingested(external_id)
    if existing:
        logger.info("YouTube video already ingested: %s", existing)
        return existing

    now = datetime.now(timezone.utc).isoformat()
    doc: dict = {
        "external_id": external_id,
        "type": "video",
        "url": url,
        "title": f"YouTube Video {video_id}",
        "published_at": None,
        "discovered_at": now,
        "duration_seconds": None,
        "thumbnail_url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        "summary": "",
        "interest_score": None,
        "interest_reasoning": "",
        "content_markdown": "",
        "content_dlp_cache_id": "",
        "transcript": None,
        "consumed": False,
        "user_interest": None,
        "metadata": {"description": "", "author": "", "tags": [], "extras": {}},
    }

    # Fetch YouTube metadata via content-dlp (metadata only, no audio)
    channel_name = "Unknown Channel"
    channel_url = ""
    try:
        yt_data = await content_dlp.fetch_youtube(url, no_audio=True, transcript=False)
        doc["title"] = yt_data.get("title") or doc["title"]
        doc["thumbnail_url"] = yt_data.get("thumbnail_url") or doc["thumbnail_url"]
        if yt_data.get("published_date"):
            doc["published_at"] = _normalize_date_to_iso(yt_data["published_date"])
        if yt_data.get("duration_seconds"):
            doc["duration_seconds"] = yt_data["duration_seconds"]
        doc["metadata"]["description"] = yt_data.get("description", "")
        doc["metadata"]["author"] = yt_data.get("author", "")
        channel_name = yt_data.get("author") or channel_name
        channel_url = (yt_data.get("extras") or {}).get("channel_url", "")
    except Exception as e:
        logger.warning("content-dlp YouTube metadata fetch failed for %s: %s", url, e)

    # Get/create subscription for the channel
    sub_url = channel_url or f"https://www.youtube.com/channel/unknown_{video_id}"
    doc["subscription_id"] = await _get_or_create_source_subscription(
        subscription_type="youtube_channel",
        url=sub_url,
        name=channel_name,
    )

    # Fetch captions via yt-dlp
    try:
        from backend.app.services.youtube_captions import fetch_video_metadata
        meta = fetch_video_metadata(url)
        if meta and meta.get("is_live"):
            raise ValueError("Cannot ingest active livestream")
        if meta:
            if meta.get("duration"):
                doc["duration_seconds"] = meta["duration"]
            if meta.get("captions"):
                doc["transcript"] = meta["captions"]
    except ValueError:
        raise
    except Exception as e:
        logger.warning("yt-dlp metadata fetch failed for %s: %s", url, e)

    # Fall back to content-dlp transcription if no captions
    if not doc.get("transcript"):
        try:
            logger.info("Falling back to content-dlp transcription for %s", url)
            yt_transcript = await content_dlp.fetch_youtube(url, no_audio=False, transcript=True)
            if yt_transcript.get("transcript"):
                t = yt_transcript["transcript"]
                doc["transcript"] = t if isinstance(t, dict) else {"text": t, "chunks": []}
        except Exception as e:
            logger.warning("content-dlp transcription failed for %s: %s", url, e)

    # Derive duration from transcript chunks if still missing
    if not doc.get("duration_seconds"):
        t = doc.get("transcript")
        if isinstance(t, dict) and t.get("chunks"):
            last_end = t["chunks"][-1].get("end", 0)
            if last_end > 0:
                doc["duration_seconds"] = last_end

    return await _summarize_and_index(doc)


async def _ingest_audio(url: str) -> str:
    external_id = f"audio_{hashlib.md5(url.encode()).hexdigest()[:12]}"

    existing = await _check_already_ingested(external_id)
    if existing:
        logger.info("Audio already ingested: %s", existing)
        return existing

    parsed = urlparse(url)
    domain = parsed.netloc
    filename = parsed.path.split("/")[-1]
    now = datetime.now(timezone.utc).isoformat()

    doc: dict = {
        "external_id": external_id,
        "type": "podcast_episode",
        "url": url,
        "title": filename,
        "published_at": None,
        "discovered_at": now,
        "duration_seconds": None,
        "thumbnail_url": "",
        "summary": "",
        "interest_score": None,
        "interest_reasoning": "",
        "content_markdown": "",
        "content_dlp_cache_id": "",
        "transcript": None,
        "consumed": False,
        "user_interest": None,
        "metadata": {"description": "", "author": "", "tags": [], "extras": {"enclosure_url": url}},
    }

    doc["subscription_id"] = await _get_or_create_source_subscription(
        subscription_type="podcast",
        url=f"{parsed.scheme}://{domain}",
        name=domain,
    )

    try:
        logger.info("Downloading and transcribing audio: %s", url)
        transcript_data = await content_dlp.download_and_transcribe(url)
        if transcript_data.get("transcript"):
            t = transcript_data["transcript"]
            doc["transcript"] = t if isinstance(t, dict) else {"text": t, "chunks": []}
    except Exception as e:
        logger.warning("Failed to transcribe audio %s: %s", url, e)

    # Derive duration from transcript chunks
    if not doc.get("duration_seconds"):
        t = doc.get("transcript")
        if isinstance(t, dict) and t.get("chunks"):
            last_end = t["chunks"][-1].get("end", 0)
            if last_end > 0:
                doc["duration_seconds"] = last_end

    return await _summarize_and_index(doc)


async def _ingest_article(url: str) -> str:
    external_id = f"article_{hashlib.md5(url.encode()).hexdigest()[:12]}"

    existing = await _check_already_ingested(external_id)
    if existing:
        logger.info("Article already ingested: %s", existing)
        return existing

    parsed = urlparse(url)
    domain = parsed.netloc
    now = datetime.now(timezone.utc).isoformat()

    doc: dict = {
        "external_id": external_id,
        "type": "article",
        "url": url,
        "title": url,
        "published_at": None,
        "discovered_at": now,
        "duration_seconds": None,
        "thumbnail_url": "",
        "summary": "",
        "interest_score": None,
        "interest_reasoning": "",
        "content_markdown": "",
        "content_dlp_cache_id": "",
        "transcript": None,
        "consumed": False,
        "user_interest": None,
        "metadata": {"description": "", "author": "", "tags": [], "extras": {}},
    }

    # Fetch HTML metadata for title, description, published date
    html_meta = await _fetch_html_metadata(url)
    doc["title"] = html_meta.get("title") or url
    doc["thumbnail_url"] = html_meta.get("thumbnail_url", "")
    doc["metadata"]["description"] = html_meta.get("description", "")
    doc["metadata"]["author"] = html_meta.get("author", "")
    if html_meta.get("published_at"):
        doc["published_at"] = _normalize_date_to_iso(html_meta["published_at"])

    # Determine source subscription
    author = html_meta.get("author", "")
    source_name = _build_article_source_name(domain, author)
    doc["subscription_id"] = await _get_or_create_source_subscription(
        subscription_type="rss",
        url=f"{parsed.scheme}://{domain}",
        name=source_name,
    )

    # Scrape full page content
    try:
        scraped = await content_dlp.fetch_webscrape(url)
        doc["content_markdown"] = scraped.get("markdown", "")
        doc["content_dlp_cache_id"] = scraped.get("content_id", "")
    except Exception as e:
        logger.warning("Failed to scrape %s: %s", url, e)

    # Clean up scraped markdown
    if doc.get("content_markdown"):
        try:
            from backend.app.services.content_cleanup import cleanup_article_markdown
            result = await cleanup_article_markdown(doc["content_markdown"], doc["title"])
            doc["content_markdown"] = result["markdown"]
            if not doc.get("thumbnail_url") and result.get("image_url"):
                doc["thumbnail_url"] = result["image_url"]
        except Exception as e:
            logger.warning("Failed to clean up article markdown for %s: %s", url, e)

    return await _summarize_and_index(doc)


async def _summarize_and_index(doc: dict) -> str:
    """Generate AI summary and index the document to ES. Returns new content item id."""
    transcript_obj = doc.get("transcript")
    transcript_text = ""
    if isinstance(transcript_obj, dict):
        transcript_text = transcript_obj.get("text", "")

    source_text = transcript_text or doc.get("content_markdown", "")
    description = doc.get("metadata", {}).get("description", "")

    if source_text or description:
        try:
            from backend.app.services.summarizer import summarize_content
            summary = await summarize_content(
                title=doc["title"],
                content_type=doc["type"],
                transcript_text=source_text,
                description=description,
                author=doc.get("metadata", {}).get("author", ""),
            )
            if summary:
                doc["summary"] = summary
        except Exception as e:
            logger.warning("Failed to summarize content '%s': %s", doc["title"], e)

    es = get_es_client()
    doc_id = str(uuid.uuid4())
    await es.index(index=CONTENT_ITEMS_INDEX, id=doc_id, document=doc)
    logger.info("Ingested individual content: '%s' → %s", doc["title"], doc_id)
    return doc_id
