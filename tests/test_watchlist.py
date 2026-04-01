"""Integration tests for the watchlist endpoints.

Requires:
  - Backend running on localhost:3103
  - Elasticsearch accessible and indices created
"""

import time

import httpx
import pytest


RICKROLL_URL = "https://www.youtube.com/watch?v=Aq5WXmQQooo"
RICKROLL_EXTERNAL_ID = "yt_Aq5WXmQQooo"


def test_add_and_delete_adhoc_video(api: httpx.Client):
    """POST a YouTube video for processing, wait for it to appear, then delete it."""

    # Submit the video
    resp = api.post("/api/submit_video/", json={"urls": [RICKROLL_URL]})
    assert resp.status_code == 200
    body = resp.json()

    # It should be accepted (or skipped if it already exists from a prior run)
    assert RICKROLL_URL in body["accepted"] or RICKROLL_URL in body["skipped"]
    assert body["errors"] == []

    # If it was accepted, poll until it appears in ES (background processing)
    if RICKROLL_URL in body["accepted"]:
        _wait_for_video(api, RICKROLL_EXTERNAL_ID, timeout=180)

    # Delete by external_id
    resp = api.delete(f"/api/content/by-external-id/{RICKROLL_EXTERNAL_ID}/")
    assert resp.status_code == 200
    result = resp.json()
    assert result["status"] == "deleted"
    assert result["external_id"] == RICKROLL_EXTERNAL_ID

    # Confirm it's gone
    resp = api.delete(f"/api/content/by-external-id/{RICKROLL_EXTERNAL_ID}/")
    assert resp.status_code == 404


def test_watchlist_returns_unwatched_videos(api: httpx.Client):
    """GET /api/watchlist/ should return a non-empty list of unwatched YouTube videos."""
    resp = api.get("/api/watchlist/")
    assert resp.status_code == 200

    items = resp.json()
    assert isinstance(items, list)
    assert len(items) > 0, "Watchlist is empty — expected at least one unwatched video"

    # Every item should be a video and not consumed, with no heavy fields
    for item in items:
        assert item["type"] == "video"
        assert item["consumed"] is False or "consumed" not in item
        assert "summary" not in item
        assert "transcript" not in item
        assert "content_markdown" not in item


# ---- helpers ----------------------------------------------------------------


def _find_video(api: httpx.Client, external_id: str) -> str | None:
    """Search for a video by external_id and return its ES doc id."""
    resp = api.get("/api/content/", params={"content_type": "video", "size": 200})
    if resp.status_code != 200:
        return None
    for item in resp.json().get("items", []):
        if item.get("external_id") == external_id:
            return item["id"]
    return None


def _wait_for_video(
    api: httpx.Client, external_id: str, timeout: int = 180, interval: int = 5
) -> str | None:
    """Poll until a video with the given external_id appears, or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        item_id = _find_video(api, external_id)
        if item_id:
            return item_id
        time.sleep(interval)
    return None
