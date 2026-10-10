"""Metadata-only client for Corridor Digital (corridordigital.com).

Corridor runs a white-label subscription video platform with no RSS, sitemap, or
public API, but its own frontend reads catalog data from unauthenticated JSON
endpoints on `content-cache.corridordigital.com`:

- `channels/v16/season/{id}` — a show ("season") with every episode in it
- `v3/metadata/{id}`        — one video's title, HTML description, season id

Playback (Widevine DRM) and anything personal need a logged-in account, so
AI Tube never fetches video, audio, or subtitles. It only indexes metadata and
sends the user to `corridordigital.com/video/{id}` to watch. These endpoints
are undocumented and versioned, so every parser here tolerates missing fields.

A video can appear in several seasons (e.g. both "VFX Artists React" and the
"Extended Reacts" collection); its metadata `seasonId` is the primary show.
"""

import logging
import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

from backend.app.services.feed_text import clean_feed_text

logger = logging.getLogger(__name__)

SITE_URL = "https://www.corridordigital.com"
_API_BASE = "https://content-cache.corridordigital.com"
_HEADERS = {"User-Agent": "AITube/0.1", "Accept": "application/json"}
_TIMEOUT = 15

EXTERNAL_ID_PREFIX = "corridor_"

_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?corridordigital\.com/(season|video)/(\d+)", re.IGNORECASE
)


def parse_url(url: str) -> tuple[str, int] | None:
    """Return ("season" | "video", id) for a Corridor URL, else None."""
    m = _URL_RE.match(url.strip())
    return (m.group(1).lower(), int(m.group(2))) if m else None


def season_url(season_id: int) -> str:
    return f"{SITE_URL}/season/{season_id}"


def video_url(media_id: int) -> str:
    return f"{SITE_URL}/video/{media_id}"


def external_id(media_id: int | str) -> str:
    return f"{EXTERNAL_ID_PREFIX}{media_id}"


async def _get_json(path: str) -> Any:
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(f"{_API_BASE}/{path}", headers=_HEADERS)
        resp.raise_for_status()
    return resp.json()


async def fetch_season(season_id: int) -> dict[str, Any]:
    """Fetch a show and its full episode list."""
    return await _get_json(f"channels/v16/season/{season_id}")


async def fetch_metadata(media_id: int) -> dict[str, Any]:
    """Fetch one video's metadata record (the `metadata` object of the response)."""
    data = await _get_json(f"v3/metadata/{media_id}")
    return data.get("metadata") or {}


def html_to_text(value: str | None) -> str:
    """Flatten Corridor's description HTML (a full document with <head>/<style>) to text."""
    if not value:
        return ""
    soup = BeautifulSoup(value, "html.parser")
    for tag in soup(["head", "style", "script", "link", "meta"]):
        tag.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    return clean_feed_text(soup.get_text("\n\n"))


def parse_duration(value: str | None) -> float | None:
    """'56:55' or '1:02:03' -> seconds."""
    if not value:
        return None
    try:
        parts = [int(p) for p in value.strip().split(":")]
    except ValueError:
        return None
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + p
    return float(seconds) if seconds > 0 else None


def _thumbnail(images: list[dict[str, Any]] | None) -> str:
    by_type = {i.get("type"): i.get("url") for i in images or [] if i.get("url")}
    return by_type.get("thumbnail") or by_type.get("hero-web") or by_type.get("packshot") or ""


def media_to_item(media: dict[str, Any], show_name: str, season_id: int | None) -> dict[str, Any]:
    """Map a season-listing media entry to the raw item shape `_parse_dlp_item` expects."""
    media_id = media.get("mediaId") or media.get("id")
    return {
        "content_id": external_id(media_id),
        "title": media.get("title") or "",
        "url": video_url(media_id),
        "published_date": media.get("datePublished"),
        "duration_seconds": parse_duration(media.get("duration")),
        "thumbnail_url": _thumbnail(media.get("images")),
        "description": html_to_text(media.get("shortDescription")),
        "author": "Corridor Digital",
        "tags": [],
        "extras": {
            "platform": "corridor",
            "media_id": media_id,
            "season_id": season_id,
            "show_name": show_name,
            "exclusive": bool(media.get("exclusive")),
        },
    }


def season_items(season: dict[str, Any], limit: int | None = None) -> list[dict[str, Any]]:
    """Episodes of a fetched season as raw items, newest first."""
    season_id = season.get("seasonId") or season.get("id")
    name = season.get("name") or "Corridor Digital"
    media = [m for m in season.get("media") or [] if m.get("mediaId") or m.get("id")]
    media.sort(key=lambda m: m.get("datePublished") or "", reverse=True)
    if limit is not None:
        media = media[:limit]
    return [media_to_item(m, name, season_id) for m in media]


async def fetch_show_items(url: str, limit: int = 20) -> list[dict[str, Any]]:
    """Newest episodes for a subscription URL (a /season/{id} URL)."""
    parsed = parse_url(url)
    if not parsed or parsed[0] != "season":
        raise ValueError(f"Not a Corridor show URL: {url}")
    return season_items(await fetch_season(parsed[1]), limit=limit)


async def fetch_video_item(media_id: int) -> dict[str, Any]:
    """Raw item for a single video, enriched from its primary show's listing.

    The metadata record lacks duration and the thumbnail variants, so look the
    video up in its season listing; fall back to the metadata alone if that fails.
    """
    meta = await fetch_metadata(media_id)
    season_id = int(meta["seasonId"]) if str(meta.get("seasonId") or "").isdigit() else None

    item: dict[str, Any] | None = None
    if season_id is not None:
        try:
            season = await fetch_season(season_id)
            match = next(
                (m for m in season.get("media") or [] if (m.get("mediaId") or m.get("id")) == media_id),
                None,
            )
            if match:
                item = media_to_item(match, season.get("name") or "Corridor Digital", season_id)
        except Exception as e:
            logger.warning("Corridor season %s lookup failed for video %s: %s", season_id, media_id, e)

    if item is None:
        item = media_to_item(
            {"mediaId": media_id, "title": meta.get("title"), "datePublished": meta.get("datePublished")},
            "Corridor Digital",
            season_id,
        )

    # The metadata description is the full one; the listing only has a teaser.
    full = html_to_text(meta.get("description"))
    if full:
        item["description"] = full
    if meta.get("title"):
        item["title"] = meta["title"]
    return item
