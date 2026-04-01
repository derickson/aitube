"""LLM-powered metadata extraction for ad-hoc content submissions."""

import asyncio
import json
import logging
import re
from urllib.parse import unquote, urlparse

from backend.app.services.anthropic_client import get_anthropic_client, traced_messages_create

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20241022"


async def extract_podcast_metadata(transcript_text: str, audio_url: str) -> dict:
    """Extract podcast title and author from transcript text using LLM.

    Returns {"title": str, "podcast_name": str | None, "author": str | None}.
    Falls back to URL filename if LLM fails.
    """
    fallback_title = _title_from_url(audio_url)

    if not transcript_text.strip():
        return {"title": fallback_title, "podcast_name": None, "author": None}

    # Use first ~3 minutes worth of text (rough estimate: ~500 words/min spoken)
    excerpt = transcript_text[:5000]

    try:
        client = get_anthropic_client()
        response = await asyncio.to_thread(
            traced_messages_create,
            client,
            model=_MODEL,
            max_tokens=256,
            system=(
                "You extract metadata from podcast transcript excerpts. "
                "Return ONLY valid JSON with these fields:\n"
                '{"title": "episode title", "podcast_name": "show name or null", "author": "host name(s) or null"}\n'
                "If you cannot determine a field, use null. The title should be descriptive of the episode content."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Audio URL: {audio_url}\n\n"
                    f"Transcript excerpt (first ~3 minutes):\n{excerpt}"
                ),
            }],
        )
        text = response.content[0].text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        result = json.loads(text)
        return {
            "title": result.get("title") or fallback_title,
            "podcast_name": result.get("podcast_name"),
            "author": result.get("author"),
        }
    except Exception as e:
        logger.warning("Failed to extract podcast metadata via LLM: %s", e)
        return {"title": fallback_title, "podcast_name": None, "author": None}


async def extract_article_metadata(markdown_head: str, url: str) -> dict:
    """Extract article title and publish date from markdown using LLM.

    Returns {"title": str | None, "published_date": str | None} where date is ISO format.
    """
    if not markdown_head.strip():
        return {"title": None, "published_date": None}

    excerpt = markdown_head[:3000]

    try:
        client = get_anthropic_client()
        response = await asyncio.to_thread(
            traced_messages_create,
            client,
            model=_MODEL,
            max_tokens=256,
            system=(
                "You extract metadata from article markdown. "
                "Return ONLY valid JSON with these fields:\n"
                '{"title": "article title", "published_date": "YYYY-MM-DD or null"}\n'
                "Extract the main article title (not site name). "
                "For the date, look for publish dates, bylines, or date references in the text."
            ),
            messages=[{
                "role": "user",
                "content": f"URL: {url}\n\nArticle markdown (beginning):\n{excerpt}",
            }],
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        result = json.loads(text)

        published_date = result.get("published_date")
        if published_date:
            from backend.app.services.feed_poller import _normalize_date_to_iso
            published_date = _normalize_date_to_iso(published_date)

        return {
            "title": result.get("title"),
            "published_date": published_date,
        }
    except Exception as e:
        logger.warning("Failed to extract article metadata via LLM: %s", e)
        return {"title": None, "published_date": None}


def _title_from_url(url: str) -> str:
    """Extract a human-readable title from a URL's filename."""
    path = urlparse(url).path
    filename = path.rstrip("/").rsplit("/", 1)[-1] if "/" in path else path
    filename = unquote(filename)
    # Strip common extensions
    filename = re.sub(r"\.(mp3|m4a|wav|ogg|opus|mp4|webm)$", "", filename, flags=re.IGNORECASE)
    # Replace separators with spaces
    title = re.sub(r"[-_]+", " ", filename).strip()
    return title or "Untitled Podcast Episode"
