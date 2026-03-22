"""
Resolve an arbitrary URL into a subscription-ready preview.

Given a URL the user pastes from their browser address bar, this module:
1. Detects if it's a YouTube channel/video, a direct RSS/Atom feed, or a website
2. Discovers RSS/Atom feed links from HTML pages
3. Fetches metadata (name, description, thumbnail) via content-dlp or direct parsing
4. Returns a structured preview the frontend can display before subscribing
"""

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from backend.app.services import content_dlp

logger = logging.getLogger(__name__)

FEED_MIME_TYPES = {
    "application/rss+xml",
    "application/atom+xml",
    "application/xml",
    "text/xml",
    "application/rdf+xml",
}

YOUTUBE_CHANNEL_PATTERNS = [
    r"youtube\.com/(?:@[\w.-]+)",
    r"youtube\.com/channel/[\w-]+",
    r"youtube\.com/c/[\w.-]+",
    r"youtube\.com/user/[\w.-]+",
]

YOUTUBE_VIDEO_PATTERN = r"(?:youtube\.com/watch\?v=|youtu\.be/)[\w-]+"

# Apple Podcasts: extract numeric ID from URL
APPLE_PODCASTS_PATTERN = r"podcasts\.apple\.com/.+/podcast/.+/id(\d+)"

# Spotify podcast/show URLs
SPOTIFY_SHOW_PATTERN = r"open\.spotify\.com/show/[\w]+"


@dataclass
class ResolvedFeed:
    url: str
    feed_url: str
    type: str  # youtube_channel, podcast, rss
    name: str
    description: str = ""
    thumbnail_url: str = ""
    sample_items: list[dict] = field(default_factory=list)


def _is_youtube_channel(url: str) -> bool:
    return any(re.search(p, url) for p in YOUTUBE_CHANNEL_PATTERNS)


def _is_youtube_video(url: str) -> bool:
    return bool(re.search(YOUTUBE_VIDEO_PATTERN, url))


def _looks_like_feed(content_type: str, body: str) -> bool:
    ct = content_type.lower().split(";")[0].strip()
    if ct in FEED_MIME_TYPES:
        return True
    if ct in ("text/xml", "application/xml") or body.lstrip()[:100].startswith("<?xml"):
        lower = body[:500].lower()
        return "<rss" in lower or "<feed" in lower or "<rdf" in lower
    return False


def _discover_feeds_from_html(html: str, base_url: str) -> list[dict]:
    """Find RSS/Atom feed links in HTML <link> tags."""
    soup = BeautifulSoup(html, "html.parser")
    feeds = []
    for link in soup.find_all("link", rel="alternate"):
        link_type = (link.get("type") or "").lower()
        if link_type in FEED_MIME_TYPES:
            href = link.get("href", "")
            if href:
                # Resolve relative URLs
                if href.startswith("/"):
                    parsed = urlparse(base_url)
                    href = f"{parsed.scheme}://{parsed.netloc}{href}"
                elif not href.startswith("http"):
                    href = f"{base_url.rstrip('/')}/{href}"
                feeds.append({
                    "url": href,
                    "title": link.get("title", ""),
                    "type": "atom" if "atom" in link_type else "rss",
                })
    return feeds


def _extract_html_metadata(html: str) -> dict:
    """Extract title, description, and thumbnail from HTML meta tags."""
    soup = BeautifulSoup(html, "html.parser")
    meta = {}

    # Title: og:title > <title>
    og_title = soup.find("meta", property="og:title")
    if og_title:
        meta["name"] = og_title.get("content", "")
    elif soup.title:
        meta["name"] = soup.title.string or ""

    # Description: og:description > meta description
    og_desc = soup.find("meta", property="og:description")
    if og_desc:
        meta["description"] = og_desc.get("content", "")
    else:
        desc_tag = soup.find("meta", attrs={"name": "description"})
        if desc_tag:
            meta["description"] = desc_tag.get("content", "")

    # Thumbnail: og:image
    og_img = soup.find("meta", property="og:image")
    if og_img:
        meta["thumbnail_url"] = og_img.get("content", "")

    return meta


def _parse_feed_metadata(body: str) -> dict:
    """Extract title, description, thumbnail from an RSS/Atom feed XML body."""
    soup = BeautifulSoup(body, "html.parser")
    meta: dict[str, str] = {}

    # RSS: <channel><title>, <channel><description>, <channel><image><url>
    channel = soup.find("channel")
    if channel:
        title_tag = channel.find("title", recursive=False)
        if title_tag:
            meta["name"] = title_tag.get_text(strip=True)
        desc_tag = channel.find("description", recursive=False)
        if desc_tag:
            meta["description"] = desc_tag.get_text(strip=True)
        img = channel.find("image")
        if img:
            img_url = img.find("url")
            if img_url:
                meta["thumbnail_url"] = img_url.get_text(strip=True)
        # itunes:image for podcasts
        itunes_img = channel.find("itunes:image") or channel.find("image", href=True)
        if itunes_img and itunes_img.get("href"):
            meta["thumbnail_url"] = itunes_img["href"]

    # Atom: <feed><title>, <feed><subtitle>
    feed_tag = soup.find("feed")
    if feed_tag and "name" not in meta:
        title_tag = feed_tag.find("title", recursive=False)
        if title_tag:
            meta["name"] = title_tag.get_text(strip=True)
        sub_tag = feed_tag.find("subtitle", recursive=False)
        if sub_tag:
            meta["description"] = sub_tag.get_text(strip=True)

    return meta


def _strip_cdata(text: str) -> str:
    """Remove CDATA wrappers that the HTML parser doesn't handle."""
    s = text.strip()
    if s.startswith("<![CDATA[") and s.endswith("]]>"):
        s = s[9:-3].strip()
    return s


def _extract_sample_items(body: str, limit: int = 3) -> list[dict]:
    """Pull a few recent items from the feed XML for preview."""
    soup = BeautifulSoup(body, "html.parser")
    items = []

    # RSS items
    for item in soup.find_all("item")[:limit]:
        title = item.find("title")
        pub = item.find("pubdate") or item.find("pubDate")
        items.append({
            "title": _strip_cdata(title.get_text(strip=True)) if title else "Untitled",
            "published": pub.get_text(strip=True) if pub else None,
        })

    # Atom entries
    if not items:
        for entry in soup.find_all("entry")[:limit]:
            title = entry.find("title")
            pub = entry.find("published") or entry.find("updated")
            items.append({
                "title": _strip_cdata(title.get_text(strip=True)) if title else "Untitled",
                "published": pub.get_text(strip=True) if pub else None,
            })

    return items


def _apple_podcast_id(url: str) -> str | None:
    m = re.search(APPLE_PODCASTS_PATTERN, url)
    return m.group(1) if m else None


async def _resolve_apple_podcast(url: str, podcast_id: str) -> ResolvedFeed:
    """Resolve an Apple Podcasts URL via the iTunes Lookup API."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"https://itunes.apple.com/lookup?id={podcast_id}&entity=podcast"
        )
        resp.raise_for_status()
    data = resp.json()
    if not data.get("results"):
        raise ValueError(f"No podcast found for Apple ID {podcast_id}")

    pod = data["results"][0]
    feed_url = pod.get("feedUrl", "")

    # Fetch the actual feed to get sample items
    sample_items: list[dict] = []
    if feed_url:
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
                feed_resp = await client.get(feed_url, headers={"User-Agent": "AITube/0.1"})
                feed_resp.raise_for_status()
            sample_items = _extract_sample_items(feed_resp.text)
        except Exception as e:
            logger.warning("Failed to fetch feed %s for sample items: %s", feed_url, e)

    return ResolvedFeed(
        url=url,
        feed_url=feed_url,
        type="podcast",
        name=pod.get("collectionName") or pod.get("trackName", "Unknown Podcast"),
        description=pod.get("artistName", ""),
        thumbnail_url=pod.get("artworkUrl600") or pod.get("artworkUrl100", ""),
        sample_items=sample_items,
    )


def _is_spotify_show(url: str) -> bool:
    return bool(re.search(SPOTIFY_SHOW_PATTERN, url))


async def _resolve_spotify_podcast(url: str) -> ResolvedFeed:
    """Resolve a Spotify podcast URL by scraping OG metadata and searching iTunes for the RSS feed."""
    # Scrape Spotify page for the podcast name
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()

    meta = _extract_html_metadata(resp.text)
    podcast_name = meta.get("name", "")
    spotify_thumb = meta.get("thumbnail_url", "")
    spotify_desc = meta.get("description", "")

    if not podcast_name:
        return ResolvedFeed(
            url=url, feed_url="", type="podcast",
            name=url, description="Could not determine podcast name from Spotify page",
        )

    # Search iTunes for the RSS feed
    async with httpx.AsyncClient(timeout=10) as client:
        search_resp = await client.get(
            "https://itunes.apple.com/search",
            params={"term": podcast_name, "entity": "podcast", "limit": "5"},
        )
        search_resp.raise_for_status()

    results = search_resp.json().get("results", [])

    # Find the best match by name
    feed_url = ""
    itunes_name = podcast_name
    itunes_thumb = ""
    itunes_desc = ""
    for pod in results:
        if pod.get("collectionName", "").lower() == podcast_name.lower():
            feed_url = pod.get("feedUrl", "")
            itunes_name = pod.get("collectionName", podcast_name)
            itunes_thumb = pod.get("artworkUrl600") or pod.get("artworkUrl100", "")
            itunes_desc = pod.get("artistName", "")
            break
    else:
        # No exact match — use the first result if available
        if results:
            pod = results[0]
            feed_url = pod.get("feedUrl", "")
            itunes_name = pod.get("collectionName", podcast_name)
            itunes_thumb = pod.get("artworkUrl600") or pod.get("artworkUrl100", "")
            itunes_desc = pod.get("artistName", "")

    # Fetch sample items from the feed
    sample_items: list[dict] = []
    if feed_url:
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
                feed_resp = await client.get(feed_url, headers={"User-Agent": "AITube/0.1"})
                feed_resp.raise_for_status()
            sample_items = _extract_sample_items(feed_resp.text)
        except Exception as e:
            logger.warning("Failed to fetch feed %s for sample items: %s", feed_url, e)

    return ResolvedFeed(
        url=url,
        feed_url=feed_url,
        type="podcast",
        name=itunes_name or podcast_name,
        description=itunes_desc or spotify_desc,
        thumbnail_url=itunes_thumb or spotify_thumb,
        sample_items=sample_items,
    )


async def _resolve_youtube_channel(url: str) -> ResolvedFeed:
    """Resolve a YouTube channel URL by scraping its page for metadata."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
        meta = _extract_html_metadata(resp.text)
        name = meta.get("name", url)
        # Clean up " - YouTube" suffix from og:title
        if name.endswith(" - YouTube"):
            name = name[:-10].strip()
        return ResolvedFeed(
            url=url,
            feed_url=url,
            type="youtube_channel",
            name=name,
            description=meta.get("description", ""),
            thumbnail_url=meta.get("thumbnail_url", ""),
        )
    except Exception as e:
        logger.warning("Failed to scrape YouTube channel %s: %s", url, e)
        return ResolvedFeed(
            url=url,
            feed_url=url,
            type="youtube_channel",
            name=url,
            description=f"Could not fetch metadata: {e}",
        )


async def resolve_url(url: str) -> ResolvedFeed:
    """
    Take a raw URL and return a ResolvedFeed with metadata, feed URL, and type.
    """
    url = url.strip()

    # --- Apple Podcasts ---
    apple_id = _apple_podcast_id(url)
    if apple_id:
        return await _resolve_apple_podcast(url, apple_id)

    # --- Spotify Podcasts ---
    if _is_spotify_show(url):
        return await _resolve_spotify_podcast(url)

    # --- YouTube ---
    if _is_youtube_channel(url):
        return await _resolve_youtube_channel(url)

    if _is_youtube_video(url):
        try:
            raw = await content_dlp.fetch_youtube(url, no_audio=True)
            return ResolvedFeed(
                url=url,
                feed_url=url,
                type="youtube_channel",
                name=raw.get("author") or raw.get("title", "YouTube"),
                description=raw.get("description", "")[:300],
                thumbnail_url=raw.get("thumbnail_url", ""),
                sample_items=[{"title": raw.get("title", ""), "published": raw.get("published_date")}],
            )
        except Exception as e:
            logger.warning("content-dlp youtube failed for %s: %s", url, e)
            return ResolvedFeed(
                url=url, feed_url=url, type="youtube_channel",
                name=url, description=f"Could not fetch metadata: {e}",
            )

    # --- Fetch the URL to inspect ---
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        resp = await client.get(url, headers={"User-Agent": "AITube/0.1"})
        resp.raise_for_status()

    content_type = resp.headers.get("content-type", "")
    body = resp.text

    # --- Direct feed URL ---
    if _looks_like_feed(content_type, body):
        meta = _parse_feed_metadata(body)
        sample = _extract_sample_items(body)

        # Detect podcast vs RSS: podcasts have <enclosure> tags with audio
        is_podcast = bool(re.search(r'<enclosure[^>]+type=["\']audio/', body, re.IGNORECASE))
        feed_type = "podcast" if is_podcast else "rss"

        return ResolvedFeed(
            url=url,
            feed_url=url,
            type=feed_type,
            name=meta.get("name", urlparse(url).netloc),
            description=meta.get("description", ""),
            thumbnail_url=meta.get("thumbnail_url", ""),
            sample_items=sample,
        )

    # --- HTML page: discover feeds ---
    feeds = _discover_feeds_from_html(body, url)
    html_meta = _extract_html_metadata(body)

    if feeds:
        # Fetch the first discovered feed to get better metadata
        best_feed = feeds[0]
        feed_meta: dict = {}
        sample: list[dict] = []
        is_podcast = False

        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
                feed_resp = await client.get(best_feed["url"], headers={"User-Agent": "AITube/0.1"})
                feed_resp.raise_for_status()
            feed_body = feed_resp.text
            feed_meta = _parse_feed_metadata(feed_body)
            sample = _extract_sample_items(feed_body)
            is_podcast = bool(re.search(r'<enclosure[^>]+type=["\']audio/', feed_body, re.IGNORECASE))
        except Exception as e:
            logger.warning("Failed to fetch discovered feed %s: %s", best_feed["url"], e)

        feed_type = "podcast" if is_podcast else "rss"
        name = feed_meta.get("name") or html_meta.get("name", urlparse(url).netloc)
        description = feed_meta.get("description") or html_meta.get("description", "")
        thumbnail = feed_meta.get("thumbnail_url") or html_meta.get("thumbnail_url", "")

        return ResolvedFeed(
            url=url,
            feed_url=best_feed["url"],
            type=feed_type,
            name=name,
            description=description,
            thumbnail_url=thumbnail,
            sample_items=sample,
        )

    # --- Fallback: treat as a generic website / RSS candidate ---
    # Try common feed paths relative to the user-provided URL first, then domain root
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    user_path = url.rstrip("/")
    bases_to_try = [user_path] if user_path != base else [base]
    if user_path != base:
        bases_to_try.append(base)
    common_suffixes = ["/feed", "/rss", "/atom.xml", "/feed.xml", "/rss.xml", "/index.xml"]

    async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
        for b in bases_to_try:
            for suffix in common_suffixes:
                try:
                    probe_url = f"{b}{suffix}"
                    probe = await client.get(probe_url, headers={"User-Agent": "AITube/0.1"})
                    if probe.status_code == 200 and _looks_like_feed(
                        probe.headers.get("content-type", ""), probe.text
                    ):
                        feed_body = probe.text
                        feed_meta = _parse_feed_metadata(feed_body)
                        sample = _extract_sample_items(feed_body)
                        is_podcast = bool(re.search(r'<enclosure[^>]+type=["\']audio/', feed_body, re.IGNORECASE))

                        return ResolvedFeed(
                            url=url,
                            feed_url=probe_url,
                            type="podcast" if is_podcast else "rss",
                            name=feed_meta.get("name") or html_meta.get("name", parsed.netloc),
                            description=feed_meta.get("description") or html_meta.get("description", ""),
                            thumbnail_url=feed_meta.get("thumbnail_url") or html_meta.get("thumbnail_url", ""),
                            sample_items=sample,
                        )
                except Exception:
                    continue

    # Nothing found — return as generic RSS with HTML metadata
    return ResolvedFeed(
        url=url,
        feed_url=url,
        type="rss",
        name=html_meta.get("name", parsed.netloc),
        description=html_meta.get("description", ""),
        thumbnail_url=html_meta.get("thumbnail_url", ""),
    )
