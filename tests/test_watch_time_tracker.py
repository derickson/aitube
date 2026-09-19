"""Unit tests for the playhead-delta-to-watch-minutes accumulator.

Pure unit tests — no network or ES involved. `flush()` behavior (the ES
write side) is exercised via mocking, not integration.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.app.services.watch_time_tracker import (
    MAX_DELTA_SECONDS,
    WatchTimeTracker,
)


def test_first_sighting_of_an_item_records_no_delta():
    tracker = WatchTimeTracker()
    now = datetime(2026, 9, 19, 14, 0, 5, tzinfo=timezone.utc)
    tracker.record("item1", 5.0, "youtube_channel", now)
    assert tracker._hour_buckets == {}


def test_normal_tick_accumulates_full_delta():
    tracker = WatchTimeTracker()
    now = datetime(2026, 9, 19, 14, 0, 0, tzinfo=timezone.utc)
    tracker.record("item1", 0.0, "youtube_channel", now)
    tracker.record("item1", 5.0, "youtube_channel", now + timedelta(seconds=5))
    bucket = tracker._hour_buckets["2026-09-19T14:00:00+00:00"]
    assert bucket["total_seconds"] == 5.0
    assert bucket["by_type"]["youtube_channel"] == 5.0


def test_forward_seek_is_capped_not_counted_in_full():
    tracker = WatchTimeTracker()
    now = datetime(2026, 9, 19, 14, 0, 0, tzinfo=timezone.utc)
    tracker.record("item1", 0.0, "podcast", now)
    tracker.record("item1", 9999.0, "podcast", now + timedelta(seconds=5))
    bucket = tracker._hour_buckets["2026-09-19T14:00:00+00:00"]
    assert bucket["total_seconds"] == MAX_DELTA_SECONDS


def test_backward_seek_records_nothing():
    tracker = WatchTimeTracker()
    now = datetime(2026, 9, 19, 14, 0, 0, tzinfo=timezone.utc)
    tracker.record("item1", 300.0, "rss", now)
    tracker.record("item1", 10.0, "rss", now + timedelta(seconds=5))
    assert tracker._hour_buckets == {}


def test_tick_straddling_an_hour_boundary_lands_in_the_later_hour():
    # The delta is attributed to the hour of the tick that reports it, not the
    # hour watching started in — a deliberate simplification (see docstring).
    tracker = WatchTimeTracker()
    now = datetime(2026, 9, 19, 13, 59, 58, tzinfo=timezone.utc)
    tracker.record("item1", 0.0, "youtube_channel", now)
    tracker.record("item1", 5.0, "youtube_channel", now + timedelta(seconds=5))
    assert set(tracker._hour_buckets.keys()) == {
        "2026-09-19T14:00:00+00:00",
    }


def test_unknown_content_type_still_counts_toward_total_but_not_by_type():
    tracker = WatchTimeTracker()
    now = datetime(2026, 9, 19, 14, 0, 0, tzinfo=timezone.utc)
    tracker.record("item1", 0.0, None, now)
    tracker.record("item1", 5.0, None, now + timedelta(seconds=5))
    bucket = tracker._hour_buckets["2026-09-19T14:00:00+00:00"]
    assert bucket["total_seconds"] == 5.0
    assert bucket["by_type"] == {}


def test_lru_eviction_drops_oldest_item_when_over_capacity():
    tracker = WatchTimeTracker()
    now = datetime(2026, 9, 19, 14, 0, 0, tzinfo=timezone.utc)
    from backend.app.services import watch_time_tracker as module

    module.MAX_TRACKED_ITEMS = 2
    try:
        tracker.record("item1", 0.0, "rss", now)
        tracker.record("item2", 0.0, "rss", now)
        tracker.record("item3", 0.0, "rss", now)  # evicts item1
        assert "item1" not in tracker._last_position
        assert "item2" in tracker._last_position
        assert "item3" in tracker._last_position
    finally:
        module.MAX_TRACKED_ITEMS = 1000
