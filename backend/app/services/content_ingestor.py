"""Service for ingesting individual content items by URL (no subscription creation)."""
import hashlib
import logging
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse
from typing import Any

import httpx
from bs4 import BeautifulSoup

from backend.app.services.elasticsearch import CONTENT_ITEMS_INDEX, get_es_client

logger = logging.getLogger(__name__)

# Audio file extensions that indicate a direct podcast/audio URL
_AUDIO_EXTENSIONS = {".mp3", ".m4a", ".ogg", ".opus", ".wav", ".flac", ".aac"}

# Substack domains
_SUBSTACK_PATTERN = re.compile(r"\.substack\.com$", re.IGNORECASE)


def _is_youtube_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    if host in ("youtube.com", "youtu.be"):
        if host == "youtube.com" and "/watch" in parsed.path:
            return True
        if host == "youtu.be":
            return True
    return False


def _is_audio_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in _AUDIO_EXTENSIONS)


def _extract_youtube_video_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    if host == "youtu.be":
        return parsed.path.lstrip("/").split("/")[0] or None
    if host == "youtube.com":
        from urllib.parse import parse_qs
        params = parse_qs(parsed.query)
        ids = params.get("v", [])
        return ids[0] if ids else None
    return None


def _domain_from_url(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _synthetic_subscription_id(content_type: str, source_key: str) -> str:
    """Create a stable synthetic subscription_id for manually-added content.
    Does NOT correspond to any subscription record in ES."""
    slug = hashlib.md5(source_key.lower().encode()).hexdigest()[:12]
    return f"manual:{content_type}:{slug}"


async def _fetch_og_metadata(url: str) -> dict[str, Any]:
    """Fetch Open Graph / HTML metadata from a URL."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        def og(prop: str) -> str:
            tag = soup.find("meta", property=f"og:{prop}") or soup.find("meta", attrs={"name": f"og:{prop}"})
            return tag.get("content", "").strip() if tag else ""

        def meta(name: str) -> str:
            tag = soup.find("meta", attrs={"name": name})
            return tag.get("content", "").strip() if tag else ""

        title = og("title") or (soup.find("title") or "").get_text(strip=True)
        description = og("description") or meta("description")
        image = og("image")
        author = meta("author") or og("article:author")
        published = meta("article:published_time") or og("article:published_time") or og("published_time")
        site_name = og("site_name")

        return {
            "title": title,
            "description": description,
            "thumbnail_url": image,
            "author": author,
            "published_date": published,
            "site_name": site_name,
        }
    except Exception as e:
        logger.warning("Failed to fetch OG metadata from %s: %s", url, e)
        return {}


async def preview_url(url: str) -> dict[str, Any]:
    """
    Preview metadata for a URL without ingesting it.
    Returns title, thumbnail, description, source_name, content type, author, published date.
    Does not create any subscription records.
    """
    from backend.app.services import content_dlp

    if _is_youtube_url(url):
        try:
            raw = await content_dlp.fetch_youtube(url, no_audio=True, transcript=False)
            video_id = _extract_youtube_video_id(url) or ""
            return {
                "url": url,
                "detected_type": "video",
                "title": raw.get("title", "YouTube Video"),
                "description": raw.get("description", ""),
                "thumbnail_url": raw.get("thumbnail_url") or (
                    f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else ""
                ),
                "source_name": raw.get("author") or raw.get("channel") or _domain_from_url(url),
                "author": raw.get("author") or raw.get("channel", ""),
                "published_date": raw.get("published_date", ""),
                "duration_seconds": raw.get("duration_seconds"),
            }
        except Exception as e:
            logger.warning("content-dlp YouTube preview failed for %s: %s", url, e)
            og = await _fetch_og_metadata(url)
            return {
                "url": url,
                "detected_type": "video",
                "title": og.get("title", "YouTube Video"),
                "description": og.get("description", ""),
                "thumbnail_url": og.get("thumbnail_url", ""),
                "source_name": og.get("author") or _domain_from_url(url),
                "author": og.get("author", ""),
                "published_date": og.get("published_date", ""),
                "duration_seconds": None,
            }

    if _is_audio_url(url):
        domain = _domain_from_url(url)
        return {
            "url": url,
            "detected_type": "podcast_episode",
            "title": url.split("/")[-1],
            "description": "",
            "thumbnail_url": "",
            "source_name": domain,
            "author": "",
            "published_date": "",
            "duration_seconds": None,
        }

    # Web article — fetch OG metadata
    og = await _fetch_og_metadata(url)
    domain = _domain_from_url(url)
    is_substack = bool(_SUBSTACK_PATTERN.search(domain))
    author = og.get("author", "")
    site_name = og.get("site_name", "") or domain
    if is_substack and author:
        source_name = f"{author} (Substack)"
    else:
        source_name = site_name

    return {
        "url": url,
        "detected_type": "article",
        "title": og.get("title", url),
        "description": og.get("description", ""),
        "thumbnail_url": og.get("thumbnail_url", ""),
        "source_name": source_name,
        "author": author,
        "published_date": og.get("published_date", ""),
        "duration_seconds": None,
    }


async def check_already_ingested(url: str) -> str | None:
    """Return existing ES document ID if this URL has already been ingested, else None."""
    es = get_es_client()
    try:
        resp = await es.search(
            index=CONTENT_ITEMS_INDEX,
            body={
                "query": {"term": {"url": url}},
                "_source": False,
                "size": 1,
            },
        )
        hits = resp["hits"]["hits"]
        return hits[0]["_id"] if hits else None
    except Exception:
        return None


async def ingest_url(url: str) -> str:
    """
    Full ingestion pipeline for a single URL.
    Populates ES document fields (subscription_id, source_name, type, etc.)
    without creating any subscription records.
    Returns the new ES document ID.
    """
    from backend.app.services import content_dlp
    from backend.app.services.feed_poller import _normalize_date_to_iso

    existing_id = await check_already_ingested(url)
    if existing_id:
        logger.info("URL already ingested, returning existing ID: %s", existing_id)
        return existing_id

    doc: dict[str, Any] = {
        "url": url,
        "external_id": f"manual_{hashlib.md5(url.encode()).hexdigest()[:16]}",
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "summary": "",
        "interest_score": None,
        "interest_reasoning": "",
        "consumed": False,
        "transcript": None,
        "content_markdown": "",
        "content_dlp_cache_id": "",
    }

    if _is_youtube_url(url):
        await _ingest_youtube(url, doc)
    elif _is_audio_url(url):
        await _ingest_audio(url, doc)
    else:
        await _ingest_article(url, doc)

    # Generate AI summary
    transcript_obj = doc.get("transcript")
    transcript_text = ""
    if isinstance(transcript_obj, dict):
        transcript_text = transcript_obj.get("text", "")
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
            logger.warning("Failed to summarize %s: %s", doc.get("title"), e)

    es = get_es_client()
    doc_id = str(uuid.uuid4())
    await es.index(index=CONTENT_ITEMS_INDEX, id=doc_id, document=doc)
    logger.info("Ingested manual content: %s — %s", doc.get("title"), doc_id)
    return doc_id


async def _ingest_youtube(url: str, doc: dict[str, Any]) -> None:
    """Populate doc fields for a YouTube video."""
    from backend.app.services import content_dlp
    from backend.app.services.youtube_captions import fetch_video_metadata
    from backend.app.services.feed_poller import _normalize_date_to_iso

    video_id = _extract_youtube_video_id(url) or ""

    # Try yt-dlp first for captions and metadata
    captions = None
    duration = None
    author = ""
    title = "YouTube Video"
    description = ""
    published_at = None
    thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else ""

    try:
        meta = fetch_video_metadata(url)
        if meta:
            if meta.get("is_live"):
                logger.info("Skipping livestream: %s", url)
                title = meta.get("title", title)
            if meta.get("duration"):
                duration = meta["duration"]
            if meta.get("captions"):
                captions = meta["captions"]
    except Exception as e:
        logger.warning("yt-dlp metadata failed for %s: %s", url, e)

    # Fetch full metadata from content-dlp
    try:
        raw = await content_dlp.fetch_youtube(url, no_audio=True, transcript=False)
        title = raw.get("title", title)
        description = raw.get("description", description)
        author = raw.get("author") or raw.get("channel") or ""
        if not thumbnail_url:
            thumbnail_url = raw.get("thumbnail_url", "")
        pub = raw.get("published_date")
        if pub:
            published_at = _normalize_date_to_iso(pub)
        if not duration and raw.get("duration_seconds"):
            duration = raw["duration_seconds"]
    except Exception as e:
        logger.warning("content-dlp YouTube fetch failed for %s: %s", url, e)
        # Fall back to OG metadata
        og = await _fetch_og_metadata(url)
        title = og.get("title", title)
        description = og.get("description", description)
        author = og.get("author") or author
        thumbnail_url = og.get("thumbnail_url") or thumbnail_url

    # Fallback: transcribe via content-dlp if no captions
    if not captions:
        try:
            logger.info("No captions, falling back to content-dlp transcription for %s", url)
            yt_data = await content_dlp.fetch_youtube(url, no_audio=False, transcript=True)
            if yt_data.get("transcript"):
                t = yt_data["transcript"]
                captions = t if isinstance(t, dict) else {"text": t, "chunks": []}
        except Exception as e:
            logger.warning("content-dlp transcription failed for %s: %s", url, e)

    # Derive duration from transcript chunks if still missing
    if not duration and isinstance(captions, dict) and captions.get("chunks"):
        last_end = captions["chunks"][-1].get("end", 0)
        if last_end > 0:
            duration = last_end

    source_key = author or _domain_from_url(url)
    doc.update({
        "type": "video",
        "title": title,
        "published_at": published_at,
        "duration_seconds": duration,
        "thumbnail_url": thumbnail_url,
        "transcript": captions,
        "subscription_id": _synthetic_subscription_id("youtube", source_key),
        "metadata": {
            "description": description,
            "author": author,
            "source_name": author or _domain_from_url(url),
            "tags": [],
            "extras": {},
        },
    })


async def _ingest_audio(url: str, doc: dict[str, Any]) -> None:
    """Populate doc fields for a direct audio file."""
    from backend.app.services import content_dlp
    from backend.app.services.feed_poller import _normalize_date_to_iso

    domain = _domain_from_url(url)
    title = url.split("/")[-1]
    transcript = None

    try:
        transcript_data = await content_dlp.download_and_transcribe(url)
        if transcript_data.get("transcript"):
            t = transcript_data["transcript"]
            transcript = t if isinstance(t, dict) else {"text": t, "chunks": []}
    except Exception as e:
        logger.warning("Transcription failed for audio %s: %s", url, e)

    duration = None
    if isinstance(transcript, dict) and transcript.get("chunks"):
        last_end = transcript["chunks"][-1].get("end", 0)
        if last_end > 0:
            duration = last_end

    doc.update({
        "type": "podcast_episode",
        "title": title,
        "published_at": None,
        "duration_seconds": duration,
        "thumbnail_url": "",
        "transcript": transcript,
        "subscription_id": _synthetic_subscription_id("podcast", domain),
        "metadata": {
            "description": "",
            "author": "",
            "source_name": domain,
            "tags": [],
            "extras": {"enclosure_url": url},
        },
    })


async def _ingest_article(url: str, doc: dict[str, Any]) -> None:
    """Populate doc fields for a web article."""
    from backend.app.services import content_dlp
    from backend.app.services.content_cleanup import cleanup_article_markdown
    from backend.app.services.feed_poller import _normalize_date_to_iso

    domain = _domain_from_url(url)
    og = await _fetch_og_metadata(url)
    title = og.get("title") or url
    description = og.get("description", "")
    thumbnail_url = og.get("thumbnail_url", "")
    author = og.get("author", "")
    published_at = None
    if og.get("published_date"):
        published_at = _normalize_date_to_iso(og["published_date"])

    is_substack = bool(_SUBSTACK_PATTERN.search(domain))
    site_name = og.get("site_name") or domain
    if is_substack and author:
        source_name = f"{author} (Substack)"
    else:
        source_name = site_name

    content_markdown = ""
    content_dlp_cache_id = ""
    try:
        scraped = await content_dlp.fetch_webscrape(url)
        content_markdown = scraped.get("markdown", "")
        content_dlp_cache_id = scraped.get("content_id", "")
    except Exception as e:
        logger.warning("Failed to scrape article %s: %s", url, e)

    if content_markdown:
        try:
            result = await cleanup_article_markdown(content_markdown, title)
            content_markdown = result["markdown"]
            if not thumbnail_url and result.get("image_url"):
                thumbnail_url = result["image_url"]
        except Exception as e:
            logger.warning("Failed to clean up article %s: %s", title, e)

    doc.update({
        "type": "article",
        "title": title,
        "published_at": published_at,
        "duration_seconds": None,
        "thumbnail_url": thumbnail_url,
        "content_markdown": content_markdown,
        "content_dlp_cache_id": content_dlp_cache_id,
        "subscription_id": _synthetic_subscription_id("article", source_name),
        "metadata": {
            "description": description,
            "author": author,
            "source_name": source_name,
            "tags": [],
            "extras": {},
        },
    })
