"""Fetch YouTube auto-captions with timestamps via yt-dlp."""

import json
import logging
from typing import Any
from urllib.request import urlopen

import yt_dlp

logger = logging.getLogger(__name__)


def fetch_captions(video_url: str, lang: str = "en") -> dict[str, Any] | None:
    """
    Fetch auto-generated captions for a YouTube video.
    Returns {"text": "...", "chunks": [{"text": ..., "start": ..., "end": ...}, ...]}
    or None if no captions available.
    """
    ydl_opts = {
        "skip_download": True,
        "writeautomaticsub": True,
        "subtitleslangs": [lang],
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
    except Exception as e:
        logger.warning("yt-dlp failed for %s: %s", video_url, e)
        return None

    auto_captions = info.get("automatic_captions", {})
    # Also check regular subtitles
    subtitles = info.get("subtitles", {})

    # Prefer manual subs, fall back to auto
    caption_list = subtitles.get(lang) or auto_captions.get(lang)
    if not caption_list:
        # Try en-orig or similar
        for key in auto_captions:
            if key.startswith(lang):
                caption_list = auto_captions[key]
                break

    if not caption_list:
        logger.info("No captions found for %s", video_url)
        return None

    # Find json3 format for timestamps
    json3_url = None
    for fmt in caption_list:
        if fmt.get("ext") == "json3":
            json3_url = fmt["url"]
            break

    if not json3_url:
        logger.info("No json3 caption format for %s", video_url)
        return None

    try:
        data = json.loads(urlopen(json3_url).read())
    except Exception as e:
        logger.warning("Failed to fetch caption data for %s: %s", video_url, e)
        return None

    chunks = []
    full_text_parts = []

    for event in data.get("events", []):
        segs = event.get("segs", [])
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if not text:
            continue

        start_ms = event.get("tStartMs", 0)
        dur_ms = event.get("dDurationMs", 0)
        start = start_ms / 1000
        end = (start_ms + dur_ms) / 1000

        chunks.append({"text": text, "start": start, "end": end})
        full_text_parts.append(text)

    if not chunks:
        return None

    return {
        "text": " ".join(full_text_parts),
        "chunks": chunks,
    }
