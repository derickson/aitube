"""Unit tests for Dopamine Detox end-time math and the never-shorten rule."""

from datetime import datetime, timedelta, timezone

import pytest

from backend.app.services import detox

ET = detox.EASTERN


@pytest.fixture(autouse=True)
def _reset():
    detox._until = None
    yield
    detox._until = None


def et(*args) -> datetime:
    return datetime(*args, tzinfo=ET)


def test_three_hours_is_240_minutes():
    now = et(2026, 10, 31, 20, 15)
    assert detox.compute_until("3h", now) == now + timedelta(minutes=240)


@pytest.mark.parametrize("now", [et(2026, 10, 31, 0, 30), et(2026, 10, 31, 23, 59)])
def test_one_day_ends_2am_next_calendar_day(now):
    assert detox.compute_until("1d", now) == et(2026, 11, 1, 2, 0)


def test_three_days_ends_2am_after_three_midnights():
    assert detox.compute_until("3d", et(2026, 10, 31, 9, 0)) == et(2026, 11, 3, 2, 0)


def test_dst_change_still_lands_on_local_2am():
    # DST ends 2026-11-01 in US Eastern; release is still 2am wall time (EST).
    until = detox.compute_until("3d", et(2026, 10, 30, 12, 0))
    assert until == datetime(2026, 11, 2, 7, 0, tzinfo=timezone.utc)


def test_start_never_shortens_and_expires():
    now = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
    long_end = detox.start("3d", now)
    assert detox.start("3h", now) == long_end
    assert detox.active_until(now) == long_end
    assert detox.active_until(long_end) is None
