"""Integration tests for view-state persistence and the Prediction endpoint.

Requires:
  - Backend running on localhost:3103
  - Elasticsearch accessible with at least one unwatched video that has an
    engagement prediction (run backend.scripts.backfill_engagement once).

These tests mutate real documents (consumed / user_interest) but restore the
original state afterwards.
"""

import time

import httpx
import pytest


def _wait_until(fn, timeout: float = 8.0, interval: float = 0.5):
    """Poll fn() until it returns truthy or timeout; returns last value."""
    deadline = time.monotonic() + timeout
    val = fn()
    while not val and time.monotonic() < deadline:
        time.sleep(interval)
        val = fn()
    return val


def _get_item(api: httpx.Client, item_id: str) -> dict:
    resp = api.get(f"/api/content/{item_id}/")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _first_unwatched_video(api: httpx.Client) -> dict:
    resp = api.get("/api/content/", params={"content_type": "video", "consumed": "false", "size": 50})
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert items, "No unwatched videos available to test with"
    return items[0]


def _in_watchlist(api: httpx.Client, item_id: str) -> bool:
    resp = api.get("/api/content/", params={"content_type": "video", "consumed": "false", "size": 200})
    return any(i["id"] == item_id for i in resp.json()["items"])


def _in_predictions(api: httpx.Client, item_id: str) -> bool:
    data = api.get("/api/content/predictions/").json()
    ids = {i["id"] for i in data["interesting"]} | {i["id"] for i in data["not_interesting"]}
    return item_id in ids


def test_consumed_write_persists_after_refresh(api: httpx.Client):
    """The reported bug: marking a video consumed must return 200, persist in ES,
    and remove it from the unwatched watchlist after the refresh settles."""
    item = _first_unwatched_video(api)
    item_id = item["id"]
    assert item.get("consumed") in (False, None)

    try:
        # Mark consumed — this previously 500'd because the default_pipeline ran
        # on the _update and re-inference threw on the already-scored doc.
        resp = api.put(f"/api/content/{item_id}/consumed/", params={"consumed": "true"})
        assert resp.status_code == 200, f"consumed write failed: {resp.status_code} {resp.text}"

        # GET-by-id is real-time: the write must be immediately visible.
        assert _get_item(api, item_id)["consumed"] is True

        # The write uses refresh="wait_for", so by the time the PUT returned the
        # change is searchable: the item must be gone from both the cached
        # watchlist endpoint and the uncached predictions endpoint.
        gone_watchlist = _wait_until(lambda: not _in_watchlist(api, item_id))
        assert gone_watchlist, "Video still in unwatched watchlist after marking consumed"
        gone_predictions = _wait_until(lambda: not _in_predictions(api, item_id))
        assert gone_predictions, "Video still in predictions after marking consumed"
    finally:
        # Restore original unwatched state.
        api.put(f"/api/content/{item_id}/consumed/", params={"consumed": "false"})

    # And confirm the restore also persists (round-trip both directions works).
    assert _get_item(api, item_id)["consumed"] is False


def test_interest_write_persists_and_clears(api: httpx.Client):
    """Setting +/- interest must persist, and clearing it must remove the field."""
    item = _first_unwatched_video(api)
    item_id = item["id"]
    original = item.get("user_interest")

    try:
        resp = api.put(f"/api/content/{item_id}/interest/", params={"interest": "down"})
        assert resp.status_code == 200, resp.text
        assert _get_item(api, item_id)["user_interest"] == "down"

        resp = api.put(f"/api/content/{item_id}/interest/", params={"interest": "up"})
        assert resp.status_code == 200, resp.text
        assert _get_item(api, item_id)["user_interest"] == "up"

        resp = api.put(f"/api/content/{item_id}/interest/", params={"interest": "none"})
        assert resp.status_code == 200, resp.text
        assert _get_item(api, item_id).get("user_interest") in (None, "")
    finally:
        restore = original if original in ("up", "down") else "none"
        api.put(f"/api/content/{item_id}/interest/", params={"interest": restore})


def test_predictions_endpoint_partitions_by_threshold(api: httpx.Client):
    """Every unmarked item in 'interesting' scores >50%, and every unmarked item
    in 'not_interesting' scores <=50%; the two sets are disjoint."""
    resp = api.get("/api/content/predictions/")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    def score(i):
        return (i.get("engagement") or {}).get("score")

    for i in data["interesting"]:
        if i.get("user_interest") != "up":
            s = score(i)
            assert s is not None and s >= 0.5, f"interesting item below threshold: {s}"
    for i in data["not_interesting"]:
        if i.get("user_interest") != "down":
            s = score(i)
            assert s is not None and s < 0.5, f"not_interesting item above threshold: {s}"

    ids_a = {i["id"] for i in data["interesting"]}
    ids_b = {i["id"] for i in data["not_interesting"]}
    assert ids_a.isdisjoint(ids_b), "An item appears in both prediction sections"
