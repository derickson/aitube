"""Fetch YouTube auto-captions with timestamps via yt-dlp."""

import json
import logging
from typing import Any
from urllib.request import urlopen

import yt_dlp

logger = logging.getLogger(__name__)


def _parse_caption_data(info: dict[str, Any], lang: str = "en") -> dict[str, Any] | None:
    """
    Parse captions from a yt-dlp info dict (already extracted).
    Returns {"text": "...", "chunks": [...]} or None.
    """
    auto_captions = info.get("automatic_captions", {})
    subtitles = info.get("subtitles", {})

    # Prefer manual subs, fall back to auto
    caption_list = subtitles.get(lang) or auto_captions.get(lang)
    if not caption_list:
        for key in auto_captions:
            if key.startswith(lang):
                caption_list = auto_captions[key]
                break

    if not caption_list:
        return None

    # Find json3 format for timestamps
    json3_url = None
    for fmt in caption_list:
        if fmt.get("ext") == "json3":
            json3_url = fmt["url"]
            break

    if not json3_url:
        return None

    try:
        data = json.loads(urlopen(json3_url).read())
    except Exception as e:
        logger.warning("Failed to fetch caption data: %s", e)
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


def fetch_video_metadata(video_url: str, lang: str = "en") -> dict[str, Any] | None:
    """
    Fetch video metadata and captions via yt-dlp.
    Returns {"captions": {...} | None, "is_live": bool} or None on failure.
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

    is_live = info.get("is_live") is True or info.get("was_live") is True
    captions = _parse_caption_data(info, lang)
    duration = info.get("duration")

    return {"captions": captions, "is_live": is_live, "duration": duration}


def fetch_captions(video_url: str, lang: str = "en") -> dict[str, Any] | None:
    """
    Fetch auto-generated captions for a YouTube video.
    Makes a single yt-dlp call to get info + captions.
    """
    result = fetch_video_metadata(video_url, lang)
    if result is None:
        return None
    return result["captions"]
