"""Unit tests for the Corridor Digital metadata client.

Pure unit tests — no network or ES involved. Payloads mirror the shapes the
unauthenticated content-cache endpoints returned when this was written.
"""

from __future__ import annotations

import pytest

from backend.app.services import corridor


@pytest.mark.parametrize("url,expected", [
    ("https://www.corridordigital.com/video/3347", ("video", 3347)),
    ("https://www.corridordigital.com/video/3347?watchlist=false", ("video", 3347)),
    ("corridordigital.com/season/53", ("season", 53)),
    ("http://CorridorDigital.com/Season/9/", ("season", 9)),
    ("https://www.corridordigital.com/shows", None),
    ("https://www.youtube.com/watch?v=abc", None),
    ("https://evil.example/corridordigital.com/video/1", None),
])
def test_parse_url(url, expected):
    assert corridor.parse_url(url) == expected


@pytest.mark.parametrize("raw,expected", [
    ("56:55", 3415.0),
    ("1:02:03", 3723.0),
    ("0:00", None),
    ("", None),
    (None, None),
    ("live", None),
])
def test_parse_duration(raw, expected):
    assert corridor.parse_duration(raw) == expected


def test_html_to_text_drops_head_and_style():
    raw = (
        '<head><link href="https://fonts.googleapis.com/css2?family=Open+Sans" rel="stylesheet">'
        "<style>html body { margin: 0; }</style></head>"
        "<body><p>Achuta! We&#39;re back.</p><p>Second line</p></body>"
    )
    text = corridor.html_to_text(raw)
    assert "margin" not in text and "fonts.googleapis" not in text
    assert text == "Achuta! We're back.\n\nSecond line"


def _season():
    return {
        "id": 29,
        "seasonId": 29,
        "name": "VFX Artists React",
        "media": [
            {
                "id": 3342, "mediaId": 3342, "title": "Older", "duration": "20:00",
                "datePublished": "2026-09-25T12:00:00+00:00", "shortDescription": "old",
                "exclusive": False, "images": [],
            },
            {
                "id": 3347, "mediaId": 3347, "title": "Newer", "duration": "56:55",
                "datePublished": "2026-10-09T12:00:00+00:00", "shortDescription": "<p>new</p>",
                "exclusive": True,
                "images": [
                    {"type": "hero-web", "url": "https://img/hero.jpg"},
                    {"type": "thumbnail", "url": "https://img/thumb.jpg"},
                ],
            },
        ],
    }


def test_season_items_newest_first_and_mapped():
    items = corridor.season_items(_season())
    assert [i["content_id"] for i in items] == ["corridor_3347", "corridor_3342"]
    newest = items[0]
    assert newest["url"] == "https://www.corridordigital.com/video/3347"
    assert newest["duration_seconds"] == 3415.0
    assert newest["thumbnail_url"] == "https://img/thumb.jpg"
    assert newest["description"] == "new"
    assert newest["extras"] == {
        "platform": "corridor", "media_id": 3347, "season_id": 29,
        "show_name": "VFX Artists React", "exclusive": True,
    }


def test_season_items_limit_and_missing_fields():
    season = _season()
    season["media"].append({"title": "no id"})  # malformed entry is skipped
    items = corridor.season_items(season, limit=1)
    assert len(items) == 1 and items[0]["title"] == "Newer"
    assert corridor.season_items({}) == []
