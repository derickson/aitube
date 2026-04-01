"""Add Content endpoints — preview and confirm ad-hoc content submissions."""

import asyncio
import hashlib
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.app.services import content_dlp
from backend.app.services.elasticsearch import CONTENT_ITEMS_INDEX, get_es_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/add-content", tags=["add-content"])

# Track background tasks to prevent GC
_background_tasks: set[asyncio.Task] = set()

# Preview cache: preview_id -> {created_at, url, detected_type, data}
_preview_cache: dict[str, dict] = {}
_CACHE_TTL = 1800  # 30 minutes
_CACHE_MAX = 50

_YT_VIDEO_ID_RE = re.compile(
    r"(?:v=|youtu\.be/|embed/|shorts/)([a-zA-Z0-9_-]{11})"
)

_AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".ogg", ".opus"}


# --- Models ---


class PreviewRequest(BaseModel):
    url: str


class ContentPreview(BaseModel):
    preview_id: str
    url: str
    detected_type: str  # "video", "podcast_episode", "article"
    title: str | None = None
    thumbnail_url: str | None = None
    duration_seconds: float | None = None
    published_at: str | None = None
    description: str | None = None
    author: str | None = None
    file_size_bytes: int | None = None


class ConfirmRequest(BaseModel):
    preview_id: str
    title_override: str | None = None


# --- Helpers ---


def _cleanup_cache() -> None:
    """Remove expired entries and cap size."""
    now = time.time()
    expired = [k for k, v in _preview_cache.items() if now - v["created_at"] > _CACHE_TTL]
    for k in expired:
        del _preview_cache[k]
    # If still over limit, remove oldest
    while len(_preview_cache) > _CACHE_MAX:
        oldest = min(_preview_cache, key=lambda k: _preview_cache[k]["created_at"])
        del _preview_cache[oldest]


def _detect_type(url: str) -> str:
    """Detect content type from URL. Returns 'video', 'podcast_episode', or 'article'."""
    if _YT_VIDEO_ID_RE.search(url):
        return "video"
    path = urlparse(url).path.lower()
    ext = "." + path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
    if ext in _AUDIO_EXTENSIONS:
        return "podcast_episode"
    return "article"


async def _detect_type_with_head(url: str) -> str:
    """Detect type, falling back to HEAD request for ambiguous URLs."""
    simple = _detect_type(url)
    if simple != "article":
        return simple
    # For non-obvious URLs, try HEAD to check Content-Type
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.head(url)
            ct = resp.headers.get("content-type", "")
            if ct.startswith("audio/"):
                return "podcast_episode"
    except Exception:
        pass
    return "article"


def _extract_markdown_title(markdown: str) -> str | None:
    """Extract the first # heading from markdown."""
    for line in markdown.split("\n")[:20]:
        line = line.strip()
        if line.startswith("# ") and not line.startswith("## "):
            return line[2:].strip()
    return None


def _extract_markdown_image(markdown: str) -> str | None:
    """Extract the first image URL from markdown."""
    # ![alt](url) pattern
    m = re.search(r"!\[.*?\]\((https?://[^)]+)\)", markdown)
    if m:
        return m.group(1)
    # <img src="url"> pattern
    m = re.search(r'<img[^>]+src=["\']?(https?://[^"\'>\s]+)', markdown)
    if m:
        return m.group(1)
    return None


def _md5_hash(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()[:12]


# --- Endpoints ---


@router.post("/preview/", response_model=ContentPreview)
async def preview_content(request: PreviewRequest):
    """Detect content type and fetch lightweight metadata for preview."""
    _cleanup_cache()
    url = request.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    detected_type = await _detect_type_with_head(url)
    preview_id = str(uuid.uuid4())
    preview_data: dict[str, Any] = {
        "created_at": time.time(),
        "url": url,
        "detected_type": detected_type,
    }

    if detected_type == "video":
        preview = await _preview_youtube(url, preview_id)
    elif detected_type == "podcast_episode":
        preview = await _preview_podcast(url, preview_id)
    else:
        preview = await _preview_article(url, preview_id, preview_data)

    preview_data["preview"] = preview.model_dump()
    _preview_cache[preview_id] = preview_data
    return preview


async def _preview_youtube(url: str, preview_id: str) -> ContentPreview:
    from backend.app.services.youtube_captions import fetch_video_metadata

    try:
        meta = await asyncio.to_thread(fetch_video_metadata, url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch YouTube metadata: {e}")

    if meta and meta.get("is_live"):
        raise HTTPException(status_code=400, detail="Livestreams cannot be added")

    vid_match = _YT_VIDEO_ID_RE.search(url)
    video_id = vid_match.group(1) if vid_match else ""

    published_at = None
    if meta and meta.get("upload_date"):
        try:
            dt = datetime.strptime(meta["upload_date"], "%Y%m%d").replace(tzinfo=timezone.utc)
            published_at = dt.isoformat()
        except ValueError:
            pass

    return ContentPreview(
        preview_id=preview_id,
        url=url,
        detected_type="video",
        title=meta.get("title") if meta else None,
        thumbnail_url=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else None,
        duration_seconds=meta.get("duration") if meta else None,
        published_at=published_at,
        description=(meta.get("description") or "")[:500] if meta else None,
        author=meta.get("uploader") if meta else None,
    )


async def _preview_podcast(url: str, preview_id: str) -> ContentPreview:
    file_size = None
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.head(url)
            cl = resp.headers.get("content-length")
            if cl and cl.isdigit():
                file_size = int(cl)
    except Exception:
        pass

    return ContentPreview(
        preview_id=preview_id,
        url=url,
        detected_type="podcast_episode",
        title=None,
        file_size_bytes=file_size,
    )


async def _preview_article(url: str, preview_id: str, preview_data: dict) -> ContentPreview:
    try:
        scraped = await content_dlp.fetch_webscrape(url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to scrape URL: {e}")

    markdown = scraped.get("markdown", "")
    preview_data["scraped_markdown"] = markdown
    preview_data["content_dlp_cache_id"] = scraped.get("content_id", "")

    title = _extract_markdown_title(markdown)
    thumbnail = _extract_markdown_image(markdown)

    # Build a description snippet from the first non-heading paragraph
    description = None
    for line in markdown.split("\n"):
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("!") and not line.startswith("<"):
            description = line[:500]
            break

    return ContentPreview(
        preview_id=preview_id,
        url=url,
        detected_type="article",
        title=title,
        thumbnail_url=thumbnail,
        description=description,
    )


@router.post("/confirm/")
async def confirm_content(request: ConfirmRequest):
    """Confirm content submission — fires background processing."""
    _cleanup_cache()
    cached = _preview_cache.get(request.preview_id)
    if not cached:
        raise HTTPException(status_code=404, detail="Preview expired or not found. Please preview again.")

    url = cached["url"]
    detected_type = cached["detected_type"]

    # Dedup check
    es = get_es_client()
    if detected_type == "video":
        vid_match = _YT_VIDEO_ID_RE.search(url)
        if vid_match:
            external_id = f"yt_{vid_match.group(1)}"
            resp = await es.search(
                index=CONTENT_ITEMS_INDEX,
                body={"query": {"term": {"external_id": external_id}}, "_source": False, "size": 1},
            )
            if resp["hits"]["hits"]:
                raise HTTPException(status_code=409, detail="This video already exists in your library")
    else:
        resp = await es.search(
            index=CONTENT_ITEMS_INDEX,
            body={"query": {"term": {"url": url}}, "_source": False, "size": 1},
        )
        if resp["hits"]["hits"]:
            raise HTTPException(status_code=409, detail="This content already exists in your library")

    # Remove from cache
    del _preview_cache[request.preview_id]

    # Fire background task
    task = asyncio.create_task(
        _process_content(url, detected_type, request.title_override, cached)
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {"status": "accepted"}


async def _process_content(
    url: str, detected_type: str, title_override: str | None, cached: dict
) -> None:
    """Background task: process submitted content through the appropriate pipeline."""
    try:
        if detected_type == "video":
            await _process_video(url, title_override)
        elif detected_type == "podcast_episode":
            await _process_podcast(url, title_override)
        else:
            await _process_article(url, title_override, cached)
    except Exception:
        logger.exception("Failed to process ad-hoc %s: %s", detected_type, url)


async def _process_video(url: str, title_override: str | None) -> None:
    from backend.app.services.feed_poller import (
        build_adhoc_youtube_doc,
        process_youtube_video_doc,
    )

    vid_match = _YT_VIDEO_ID_RE.search(url)
    if not vid_match:
        return
    video_id = vid_match.group(1)

    doc = build_adhoc_youtube_doc(video_id, url)
    if title_override:
        doc["title"] = title_override

    enriched = await process_youtube_video_doc(doc)
    if enriched is None:
        logger.info("Skipped ad-hoc video (livestream?): %s", url)
        return

    if title_override:
        enriched["title"] = title_override

    es = get_es_client()
    doc_id = str(uuid.uuid4())
    await es.index(index=CONTENT_ITEMS_INDEX, id=doc_id, document=enriched)
    logger.info("Indexed ad-hoc video '%s' as %s", enriched.get("title", url), doc_id)


async def _process_podcast(url: str, title_override: str | None) -> None:
    from backend.app.services.metadata_extractor import extract_podcast_metadata
    from backend.app.services.summarizer import summarize_content

    # Transcribe
    logger.info("Downloading and transcribing podcast: %s", url)
    transcript_data = await content_dlp.download_and_transcribe(url)

    transcript = None
    if transcript_data.get("transcript"):
        t = transcript_data["transcript"]
        transcript = t if isinstance(t, dict) else {"text": t, "chunks": []}

    # Extract metadata from transcript
    transcript_text = ""
    transcript_chunks = None
    if transcript:
        transcript_text = transcript.get("text", "")
        if transcript.get("chunks"):
            transcript_chunks = transcript["chunks"]

    meta = await extract_podcast_metadata(transcript_text, url)

    title = title_override or meta.get("title") or "Untitled Podcast Episode"
    author = meta.get("author")

    # Derive duration from transcript chunks
    duration = None
    if transcript_chunks:
        last_end = transcript_chunks[-1].get("end", 0)
        if last_end > 0:
            duration = last_end

    # Generate summary
    summary = ""
    if transcript_text:
        try:
            summary = await summarize_content(
                title=title,
                content_type="podcast_episode",
                transcript_text=transcript_text,
                description="",
                author=author or "",
                transcript_chunks=transcript_chunks,
            ) or ""
        except Exception as e:
            logger.warning("Failed to summarize podcast %s: %s", url, e)

    doc = {
        "subscription_id": "adhoc",
        "external_id": f"adhoc_podcast_{_md5_hash(url)}",
        "type": "podcast_episode",
        "title": title,
        "url": url,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": duration,
        "thumbnail_url": "",
        "summary": summary,
        "interest_score": None,
        "interest_reasoning": "",
        "transcript": transcript,
        "content_markdown": "",
        "content_dlp_cache_id": "",
        "metadata": {
            "description": "",
            "author": author,
            "tags": [],
            "extras": {"podcast_name": meta.get("podcast_name"), "enclosure_url": url},
        },
    }

    es = get_es_client()
    doc_id = str(uuid.uuid4())
    await es.index(index=CONTENT_ITEMS_INDEX, id=doc_id, document=doc)
    logger.info("Indexed ad-hoc podcast '%s' as %s", title, doc_id)


async def _process_article(url: str, title_override: str | None, cached: dict) -> None:
    from backend.app.services.content_cleanup import cleanup_article_markdown
    from backend.app.services.metadata_extractor import extract_article_metadata
    from backend.app.services.summarizer import summarize_content

    # Use cached markdown from preview, or re-scrape
    markdown = cached.get("scraped_markdown", "")
    cache_id = cached.get("content_dlp_cache_id", "")
    if not markdown:
        try:
            scraped = await content_dlp.fetch_webscrape(url)
            markdown = scraped.get("markdown", "")
            cache_id = scraped.get("content_id", "")
        except Exception as e:
            logger.warning("Failed to scrape article %s: %s", url, e)
            return

    # Get title from markdown heading
    title = title_override or _extract_markdown_title(markdown)
    thumbnail = _extract_markdown_image(markdown)
    published_at = None

    # Use LLM to extract metadata if needed
    if not title or not published_at:
        try:
            meta = await extract_article_metadata(markdown[:3000], url)
            if not title:
                title = meta.get("title")
            if not published_at:
                published_at = meta.get("published_date")
        except Exception as e:
            logger.warning("Failed to extract article metadata: %s", e)

    title = title or "Untitled Article"

    # Clean up markdown
    try:
        result = await cleanup_article_markdown(markdown, title)
        markdown = result["markdown"]
        if not thumbnail and result.get("image_url"):
            thumbnail = result["image_url"]
    except Exception as e:
        logger.warning("Failed to clean up article %s: %s", url, e)

    # Generate summary
    summary = ""
    if markdown:
        try:
            summary = await summarize_content(
                title=title,
                content_type="article",
                transcript_text=markdown,
                description="",
                author="",
            ) or ""
        except Exception as e:
            logger.warning("Failed to summarize article %s: %s", url, e)

    doc = {
        "subscription_id": "adhoc",
        "external_id": f"adhoc_article_{_md5_hash(url)}",
        "type": "article",
        "title": title,
        "url": url,
        "published_at": published_at or datetime.now(timezone.utc).isoformat(),
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": None,
        "thumbnail_url": thumbnail or "",
        "summary": summary,
        "interest_score": None,
        "interest_reasoning": "",
        "transcript": None,
        "content_markdown": markdown,
        "content_dlp_cache_id": cache_id,
        "metadata": {"description": "", "author": None, "tags": [], "extras": {}},
    }

    es = get_es_client()
    doc_id = str(uuid.uuid4())
    await es.index(index=CONTENT_ITEMS_INDEX, id=doc_id, document=doc)
    logger.info("Indexed ad-hoc article '%s' as %s", title, doc_id)
