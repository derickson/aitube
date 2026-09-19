"""Unit tests for engagement outcome classification and the small-sample
statistics used by the Engagement analytics page.

Pure unit tests — no network or ES involved.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.app.services.engagement_analysis import (
    assign_cohort,
    bh_adjust,
    build_item_frame,
    channel_key,
    classify_article_state,
    classify_av_state,
    cochran_armitage,
    compute_channels,
    compute_decline_series,
    is_aitube_sync_quarantine_source,
    normalize_author,
    shrink_rate,
    wilson_interval,
    _watch_band,
)

NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)


def av(**overrides):
    base = dict(quarantined=False, vote=None, band="NEGLIGIBLE", consumed=False,
                viewed=False, discovered_at=NOW, settled=True)
    base.update(overrides)
    return classify_av_state(**base)


class TestWatchBand:
    def test_below_60_seconds_is_negligible_even_with_high_percent(self):
        assert _watch_band(30, 50.0) == "NEGLIGIBLE"

    def test_unknown_duration_short_position_is_negligible(self):
        assert _watch_band(30, None) == "NEGLIGIBLE"

    def test_unknown_duration_long_position_degrades_to_sampled_not_higher(self):
        assert _watch_band(600, None) == "SAMPLED"

    def test_boundaries(self):
        assert _watch_band(100, 9.9) == "NEGLIGIBLE"
        assert _watch_band(100, 24.9) == "SAMPLED"
        assert _watch_band(100, 89.9) == "SUBSTANTIAL"
        assert _watch_band(100, 90.0) == "COMPLETE"


class TestAVStateClassification:
    def test_quarantine_wins_over_everything(self):
        assert av(quarantined=True, vote="down", band="COMPLETE") == "QUARANTINED"

    def test_downvote_after_substantial_watch_is_informed_rejection(self):
        assert av(vote="down", band="SUBSTANTIAL") == "REJECTED_AFTER_WATCH"
        assert av(vote="down", band="COMPLETE") == "REJECTED_AFTER_WATCH"

    def test_downvote_without_watch_is_plain_rejection(self):
        assert av(vote="down", band="NEGLIGIBLE") == "REJECTED"

    def test_complete_is_positive_regardless_of_vote(self):
        assert av(band="COMPLETE") == "COMPLETED"
        assert av(band="COMPLETE", vote="up") == "COMPLETED"

    def test_substantial_manual_vs_partial(self):
        assert av(band="SUBSTANTIAL", consumed=True) == "FINISHED_MANUAL"
        assert av(band="SUBSTANTIAL", consumed=False) == "PARTIAL"

    def test_watched_elsewhere_pattern(self):
        # Dave's target pattern: consumed + upvoted + negligible/sampled playback here.
        assert av(band="NEGLIGIBLE", consumed=True, vote="up") == "WATCHED_ELSEWHERE"
        assert av(band="SAMPLED", consumed=True, vote="up") == "WATCHED_ELSEWHERE"

    def test_dismissed_is_consumed_with_no_vote_and_no_real_watch(self):
        assert av(band="NEGLIGIBLE", consumed=True, vote=None) == "DISMISSED"

    def test_sampled_dropped(self):
        assert av(band="SAMPLED", consumed=False, vote=None) == "SAMPLED_DROPPED"

    def test_bookmarked_requires_negligible_band(self):
        assert av(band="NEGLIGIBLE", vote="up", consumed=False) == "BOOKMARKED"
        # Sampled + upvoted + not consumed falls through to SAMPLED_DROPPED,
        # not BOOKMARKED — a deliberate methodology choice (first-match order).
        assert av(band="SAMPLED", vote="up", consumed=False) == "SAMPLED_DROPPED"

    def test_peeked_only_after_backfill_cutoff(self):
        assert av(band="NEGLIGIBLE", viewed=True, consumed=False,
                   discovered_at=datetime(2025, 6, 1, tzinfo=timezone.utc)) == "PEEKED"
        assert av(band="NEGLIGIBLE", viewed=True, consumed=False,
                   discovered_at=datetime(2024, 1, 1, tzinfo=timezone.utc)) == "PASSED_OVER"

    def test_untouched_settled_vs_pending(self):
        assert av(band="NEGLIGIBLE", settled=True) == "PASSED_OVER"
        assert av(band="NEGLIGIBLE", settled=False) == "PENDING"


class TestArticleStateClassification:
    def test_priority_order(self):
        assert classify_article_state(quarantined=True, vote="down", consumed=True, settled=True) == "QUARANTINED"
        assert classify_article_state(quarantined=False, vote="down", consumed=True, settled=True) == "REJECTED"
        assert classify_article_state(quarantined=False, vote="up", consumed=False, settled=True) == "LIKED"
        assert classify_article_state(quarantined=False, vote=None, consumed=True, settled=True) == "OPENED"
        assert classify_article_state(quarantined=False, vote=None, consumed=False, settled=True) == "PASSED_OVER"
        assert classify_article_state(quarantined=False, vote=None, consumed=False, settled=False) == "PENDING"


class TestCohortAssignment:
    def test_real_subscription_is_subscription_cohort(self):
        assert assign_cohort("sub-123", "video", None) == "subscription"

    def test_sync_paths_map_to_aitube_sync_cohort(self):
        assert assign_cohort("adhoc", "video", "api_ingest") == "adhoc_aitube_sync"
        assert assign_cohort("adhoc", "video", "submit_video") == "adhoc_aitube_sync"

    def test_manual_ui_is_manual_cohort(self):
        assert assign_cohort("adhoc", "video", "manual_ui") == "adhoc_manual"

    def test_nonvideo_adhoc_without_field_is_provably_manual(self):
        # The sync submit path is YouTube-only, so a legacy podcast/article ad-hoc
        # item can't have come from it.
        assert assign_cohort("adhoc", "podcast_episode", None) == "adhoc_manual"
        assert assign_cohort("adhoc", "article", None) == "adhoc_manual"

    def test_legacy_video_without_field_is_unclassified(self):
        assert assign_cohort("adhoc", "video", None) == "adhoc_unclassified"


class TestChannelKeyNormalization:
    def test_case_and_whitespace_and_at_sign_collapse_to_same_key(self):
        assert normalize_author("  Alex   Finn ") == normalize_author("alex finn")
        assert normalize_author("@AlexFinn") == "alexfinn"

    def test_none_and_empty_author_normalize_to_none(self):
        assert normalize_author(None) is None
        assert normalize_author("") is None

    def test_real_subscription_keyed_by_id_not_author(self):
        assert channel_key("sub-1", "Some Author") == ("sub", "sub-1")

    def test_adhoc_keyed_by_normalized_author(self):
        assert channel_key("adhoc", "Alex Finn") == ("adhoc", "alex finn")
        assert channel_key("adhoc", None)[1] != channel_key("adhoc", "alex finn")[1]


class TestQuarantineSourceMatching:
    def test_matches_known_aitube_sync_conventions_case_insensitively(self):
        assert is_aitube_sync_quarantine_source("hermes_aitube_transcript_judge")
        assert is_aitube_sync_quarantine_source("AITUBE-SYNC")
        assert not is_aitube_sync_quarantine_source("pytest")
        assert not is_aitube_sync_quarantine_source(None)


class TestWilsonInterval:
    def test_interval_contains_the_point_estimate(self):
        low, p, high = wilson_interval(8, 10)
        assert low < p < high

    def test_zero_trials_returns_zero_width(self):
        assert wilson_interval(0, 0) == (0.0, 0.0, 0.0)

    def test_small_n_produces_a_wide_interval(self):
        low, p, high = wilson_interval(1, 2)
        assert high - low > 0.4


class TestShrinkage:
    def test_shrinks_small_sample_toward_prior(self):
        # 2/2 raw = 1.0, should land well below 1.0 once shrunk toward a 0.3 prior.
        shrunk = shrink_rate(2, 2, p0=0.3, m=8)
        assert 0.3 < shrunk < 1.0

    def test_large_sample_barely_moves(self):
        shrunk = shrink_rate(80, 100, p0=0.3, m=8)
        assert abs(shrunk - 0.80) < 0.05


class TestCochranArmitage:
    def test_monotonic_decline_is_significant_and_negative(self):
        z, p = cochran_armitage([9, 7, 5, 3, 1], [10, 10, 10, 10, 10])
        assert z is not None and z < 0
        assert p is not None and p < 0.05

    def test_flat_rate_is_not_significant(self):
        z, p = cochran_armitage([5, 5, 5, 5, 5], [10, 10, 10, 10, 10])
        assert p is not None and p > 0.5

    def test_degenerate_all_or_nothing_returns_none(self):
        assert cochran_armitage([10, 10], [10, 10]) == (None, None)
        assert cochran_armitage([0, 0], [10, 10]) == (None, None)


class TestBHAdjust:
    def test_all_pass_when_all_pvalues_tiny(self):
        assert bh_adjust([0.001, 0.002, 0.003]) == [True, True, True]

    def test_none_pass_when_all_pvalues_large(self):
        assert bh_adjust([0.8, 0.9, 0.95]) == [False, False, False]

    def test_empty_input(self):
        assert bh_adjust([]) == []


def _fake_item(subscription_id="sub-A", author=None, submission_source=None,
                type_="video", days_ago=30, position=0.0, duration=600.0,
                consumed=False, viewed=False, vote=None, quarantined_at=None,
                engagement_score=None):
    return {
        "type": type_,
        "subscription_id": subscription_id,
        "metadata": {"author": author},
        "submission_source": submission_source,
        "duration_seconds": duration,
        "quarantined_at": quarantined_at,
        "user_interest": vote,
        "consumed": consumed,
        "viewed": viewed,
        "discovered_at": (NOW - timedelta(days=days_ago)).isoformat(),
        "engagement": {"score": engagement_score} if engagement_score is not None else None,
        "interest_score": None,
    }, {"position_seconds": position} if position else None


class TestComputeChannelsIntegration:
    def test_small_sample_channel_gets_shrunk_toward_corpus_rate(self):
        items = []
        # A large baseline population at a 50% positive rate (mix of completed
        # and rejected), so the corpus prior isn't degenerate at 1.0.
        for i in range(15):
            src, pb = _fake_item(subscription_id="sub-good", position=590, duration=600)
            items.append(build_item_frame(src, f"good-pos-{i}", pb, settle_days=14, now=NOW))
        for i in range(15):
            src, pb = _fake_item(subscription_id="sub-good", vote="down", position=590, duration=600)
            items.append(build_item_frame(src, f"good-neg-{i}", pb, settle_days=14, now=NOW))
        # A tiny channel with 2/2 "completed" — should NOT show 100% after shrinkage
        # toward the ~50% corpus baseline.
        for i in range(2):
            src, pb = _fake_item(subscription_id="sub-tiny", position=590, duration=600)
            items.append(build_item_frame(src, f"tiny-{i}", pb, settle_days=14, now=NOW))

        channels = compute_channels(items, subscriptions={
            "sub-good": {"name": "Good Channel"}, "sub-tiny": {"name": "Tiny Channel"},
        })
        tiny = next(c for c in channels if c["channel_id"] == "sub:sub-tiny")
        assert tiny["positive"].raw == 1.0
        assert tiny["positive"].shrunk < 1.0

    def test_ad_hoc_items_group_by_normalized_author_not_by_item(self):
        items = []
        for i in range(5):
            src, pb = _fake_item(subscription_id="adhoc", author="Alex Finn",
                                  submission_source="submit_video", position=590, duration=600)
            items.append(build_item_frame(src, f"a-{i}", pb, settle_days=14, now=NOW))
        channels = compute_channels(items, subscriptions={})
        assert len(channels) == 1
        assert channels[0]["channel_id"] == "adhoc:alex finn"
        assert channels[0]["n_total"] == 5

    def test_unknown_author_bucket_excluded_from_leaderboard(self):
        src, pb = _fake_item(subscription_id="adhoc", author=None, position=100, duration=600)
        items = [build_item_frame(src, "x", pb, settle_days=14, now=NOW)]
        channels = compute_channels(items, subscriptions={})
        assert channels == []


class TestDeclineSeries:
    def test_finds_watched_elsewhere_pattern_and_computes_trend(self):
        items = []
        # Declining rate_of_upvotes over 6 months: month i has (6-i) MWE out of a
        # constant 10 up-votes, plus enough non-upvoted filler so consumed-denominator exists.
        for month_idx in range(6):
            days_ago = 200 - month_idx * 30
            n_mwe = 6 - month_idx
            for j in range(n_mwe):
                src, pb = _fake_item(days_ago=days_ago, consumed=True, vote="up", position=5, duration=600)
                items.append(build_item_frame(src, f"mwe-{month_idx}-{j}", pb, settle_days=14, now=NOW))
            for j in range(10 - n_mwe):
                src, pb = _fake_item(days_ago=days_ago, consumed=True, vote="up", position=590, duration=600)
                items.append(build_item_frame(src, f"other-{month_idx}-{j}", pb, settle_days=14, now=NOW))

        result = compute_decline_series(items, now=NOW, settle_days=14)
        assert len(result["months"]) >= 5
        assert result["trend"]["ca_z"] is not None
        assert result["trend"]["ca_z"] < 0  # declining
