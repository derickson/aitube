import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Generous timeout — transcription can take minutes
_client_timeout = httpx.Timeout(timeout=600, connect=10)


def _base_url() -> str:
    from backend.app.config import settings
    return settings.content_dlp_url


async def _post(endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST to content-dlp HTTP service and return parsed JSON."""
    url = f"{_base_url()}/{endpoint}"
    logger.info("content-dlp request: POST %s %s", url, payload)

    async with httpx.AsyncClient(timeout=_client_timeout) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()

    return resp.json()


async def fetch_youtube(url: str, *, no_audio: bool = True, transcript: bool = False) -> dict[str, Any]:
    """Fetch YouTube video metadata (and optionally transcript)."""
    return await _post("youtube", {
        "url": url,
        "no_audio": no_audio,
        "transcript": transcript,
    })


async def fetch_podcast(
    feed_url: str,
    *,
    episodes: int = 5,
    no_audio: bool = True,
    transcript: bool = False,
) -> dict[str, Any]:
    """Fetch recent podcast episodes from an RSS feed."""
    return await _post("podcast", {
        "url": feed_url,
        "episodes": episodes,
        "no_audio": no_audio,
        "transcript": transcript,
    })


async def download_and_transcribe(audio_url: str) -> dict[str, Any]:
    """Download audio and transcribe it via content-dlp on the host."""
    result = await _post("transcribe", {"audio_url": audio_url})

    # content-dlp transcribe returns {text, chunks, model} at top level
    # Wrap it in a transcript key for consistency with other subcommands
    if "transcript" not in result and "text" in result:
        result = {"transcript": {"text": result.get("text", ""), "chunks": result.get("chunks", [])}}

    return result


async def fetch_webscrape(url: str) -> dict[str, Any]:
    """Scrape a web page and return markdown content via Jina Reader."""
    jina_url = f"https://r.jina.ai/{url}"
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(
            jina_url,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
    data = resp.json()
    article_data = data.get("data", {})
    return {
        "markdown": article_data.get("content", ""),
        "title": article_data.get("title", ""),
        "content_id": "",
    }
