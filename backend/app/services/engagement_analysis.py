"""Engagement analytics: per-item outcome classification, per-channel/per-cohort
statistics with small-sample shrinkage, and the "voted up but not watched here"
decline metric.

Pure computation over already-fetched data — no Elasticsearch I/O here (see
backend/app/routers/engagement.py for the fetch + wiring). Scoped to
`type == "video"` items for full statistical treatment; podcast/article items
are counted but not put through per-channel/per-state analysis (see the
"Cut or collapse for v1" note this design carries — the corpus's podcast/
article N is too small for per-channel inference).

Methodology reference: state table, signal definitions, shrinkage formula,
and statistical tests below were specified by a design pass over this exact
data model — field names and thresholds are not arbitrary, see comments at
each threshold for the one-line justification.
"""

from __future__ import annotations

import bisect
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np
from scipy import stats as sstats

# ---- thresholds (see methodology: each is a deliberate, justified choice) ----

NEGLIGIBLE_MAX_SECONDS = 60.0  # below any plausible real watch, above 5s-rounding noise
NEGLIGIBLE_MAX_PCT = 10.0
SAMPLED_MAX_PCT = 25.0  # genuine sample vs. a meaningful chunk
COMPLETE_MIN_PCT = 90.0  # the app's own auto-consume threshold

MIN_ELIGIBLE_FOR_LEADERBOARD = 3
CONFIDENT_WILSON_WIDTH = 0.5  # ~ n >= 12
MIN_SHRINKAGE_M, MAX_SHRINKAGE_M = 4.0, 30.0
FALLBACK_SHRINKAGE_M = 8.0
BH_Q = 0.10
MIN_EFFECT_PP = 0.10  # 10 percentage points, dual-gate with BH significance

# Quarantine `source` is caller-supplied free text, not an enum — match by
# case-insensitive substring against known aitube-sync/Hermes conventions.
AITUBE_SYNC_SOURCE_MARKERS = ("aitube-sync", "aitube_sync", "aitubesync", "hermes", "rex")

# viewed=true was broadly backfilled onto pre-existing items; PEEKED (opened,
# didn't play) is only meaningful for items discovered after that backfill.
# No exact backfill date is recorded, so items are treated as "post-backfill"
# once playback tracking existed at all — approximated via a settable cutoff.
VIEWED_BACKFILL_CUTOFF = datetime(2025, 1, 1, tzinfo=timezone.utc)

STATE_BUCKET = {
    "COMPLETED": "positive",
    "FINISHED_MANUAL": "positive",
    "WATCHED_ELSEWHERE": "positive",
    "PARTIAL": "leaning_positive",
    "BOOKMARKED": "leaning_positive",
    "PEEKED": "leaning_negative",
    "DISMISSED": "leaning_negative",
    "SAMPLED_DROPPED": "leaning_negative",
    "PASSED_OVER": "leaning_negative",
    "REJECTED": "negative",
    "REJECTED_AFTER_WATCH": "negative",
    "QUARANTINED": "excluded",
    "PENDING": "excluded",
    "LIKED": "positive",
    "OPENED": "leaning_positive",
}

STATE_LABELS = {
    "COMPLETED": "Completed (≥90% watched)",
    "FINISHED_MANUAL": "Finished, marked watched (25–89%)",
    "WATCHED_ELSEWHERE": "Voted up + marked watched, no playback here",
    "PARTIAL": "Partial watch (25–89%), not marked watched",
    "BOOKMARKED": "Voted up, not watched",
    "PEEKED": "Opened, didn't play",
    "DISMISSED": "Marked watched with no real playback (ambiguous)",
    "SAMPLED_DROPPED": "Sampled (10–25%), dropped",
    "PASSED_OVER": "Never touched, aged out (negative-leaning)",
    "REJECTED": "Voted down",
    "REJECTED_AFTER_WATCH": "Voted down after substantial watch",
    "QUARANTINED": "Removed by the external transcript judge",
    "PENDING": "Too recent to judge",
    "LIKED": "Voted up (article)",
    "OPENED": "Opened, no vote (article)",
}

EV_WEIGHTS = {
    "COMPLETED": 1.0,
    "FINISHED_MANUAL": 0.9,
    "WATCHED_ELSEWHERE": 0.6,
    "PARTIAL": 0.5,
    "BOOKMARKED": 0.2,
    "PEEKED": -0.1,
    "PASSED_OVER": -0.25,
    "SAMPLED_DROPPED": -0.3,
    "DISMISSED": -0.5,
    "REJECTED": -1.0,
    "REJECTED_AFTER_WATCH": -1.0,
    "LIKED": 1.0,
    "OPENED": 0.3,
}
UPVOTE_EV_BONUS = 0.2

POSITIVE_STATES = {"COMPLETED", "FINISHED_MANUAL", "WATCHED_ELSEWHERE", "PARTIAL", "BOOKMARKED", "LIKED"}
STRONG_POSITIVE_STATES = {"COMPLETED", "FINISHED_MANUAL", "WATCHED_ELSEWHERE"}
NEGATIVE_STATES = {"REJECTED", "REJECTED_AFTER_WATCH", "DISMISSED", "SAMPLED_DROPPED", "PASSED_OVER", "PEEKED"}
EXPLICIT_NEGATIVE_STATES = {"REJECTED", "REJECTED_AFTER_WATCH"}

COHORT_ORDER = ["subscription", "adhoc_aitube_sync", "adhoc_manual", "adhoc_unclassified"]
COHORT_LABELS = {
    "subscription": "Subscriptions",
    "adhoc_aitube_sync": "aitube-sync picks",
    "adhoc_manual": "My pastes",
    "adhoc_unclassified": "Legacy ad-hoc (unclassified)",
}


# ---- per-item derivation ----


def normalize_author(author: str | None) -> str | None:
    if not author:
        return None
    s = " ".join(author.strip().casefold().split()).lstrip("@")
    return s or None


def channel_key(subscription_id: str, author: str | None) -> tuple[str, str]:
    if subscription_id and subscription_id != "adhoc":
        return ("sub", subscription_id)
    norm = normalize_author(author)
    return ("adhoc", norm or "\u0000unknown")


def assign_cohort(subscription_id: str, item_type: str, submission_source: str | None) -> str:
    if subscription_id and subscription_id != "adhoc":
        return "subscription"
    if submission_source in ("api_ingest", "submit_video"):
        return "adhoc_aitube_sync"
    if submission_source == "manual_ui":
        return "adhoc_manual"
    if item_type != "video":
        # The aitube-sync submit path is YouTube-only, so a non-video ad-hoc
        # item predating submission_source is provably a manual paste.
        return "adhoc_manual"
    return "adhoc_unclassified"


def is_aitube_sync_quarantine_source(source: str | None) -> bool:
    if not source:
        return False
    s = source.casefold()
    return any(marker in s for marker in AITUBE_SYNC_SOURCE_MARKERS)


def _watch_band(pos: float, wp: float | None) -> str:
    if wp is None:
        # Duration unknown: degrade to position-only, capped below SUBSTANTIAL
        # since depth can't be confirmed without a denominator.
        return "NEGLIGIBLE" if pos < NEGLIGIBLE_MAX_SECONDS else "SAMPLED"
    if pos < NEGLIGIBLE_MAX_SECONDS or wp < NEGLIGIBLE_MAX_PCT:
        return "NEGLIGIBLE"
    if wp < SAMPLED_MAX_PCT:
        return "SAMPLED"
    if wp < COMPLETE_MIN_PCT:
        return "SUBSTANTIAL"
    return "COMPLETE"


def classify_av_state(
    *, quarantined: bool, vote: str | None, band: str, consumed: bool, viewed: bool,
    discovered_at: datetime | None, settled: bool,
) -> str:
    if quarantined:
        return "QUARANTINED"
    if vote == "down" and band in ("SUBSTANTIAL", "COMPLETE"):
        return "REJECTED_AFTER_WATCH"
    if vote == "down":
        return "REJECTED"
    if band == "COMPLETE":
        return "COMPLETED"
    if band == "SUBSTANTIAL" and consumed:
        return "FINISHED_MANUAL"
    if band == "SUBSTANTIAL":
        return "PARTIAL"
    if band in ("NEGLIGIBLE", "SAMPLED") and consumed and vote == "up":
        return "WATCHED_ELSEWHERE"
    if band in ("NEGLIGIBLE", "SAMPLED") and consumed and vote is None:
        return "DISMISSED"
    if band == "SAMPLED" and not consumed:
        return "SAMPLED_DROPPED"
    if band == "NEGLIGIBLE" and vote == "up" and not consumed:
        return "BOOKMARKED"
    if (
        band == "NEGLIGIBLE" and viewed and not consumed
        and discovered_at is not None and discovered_at >= VIEWED_BACKFILL_CUTOFF
    ):
        return "PEEKED"
    if settled:
        return "PASSED_OVER"
    return "PENDING"


def classify_article_state(*, quarantined: bool, vote: str | None, consumed: bool, settled: bool) -> str:
    if quarantined:
        return "QUARANTINED"
    if vote == "down":
        return "REJECTED"
    if vote == "up":
        return "LIKED"
    if consumed:
        return "OPENED"
    if settled:
        return "PASSED_OVER"
    return "PENDING"


def item_ev(state: str, vote: str | None) -> float:
    base = EV_WEIGHTS.get(state, 0.0)
    if state != "REJECTED" and state != "REJECTED_AFTER_WATCH" and vote == "up":
        base = min(1.0, base + UPVOTE_EV_BONUS)
    return base


@dataclass
class ItemFrame:
    id: str
    type: str
    title: str
    url: str
    subscription_id: str
    author: str | None
    submission_source: str | None
    discovered_at: datetime | None
    duration: float | None
    position: float
    wp: float | None
    quarantined: bool
    quarantine_source: str | None
    vote: str | None
    consumed: bool
    viewed: bool
    engagement_score: float | None
    interest_score: float | None
    settled: bool
    state: str
    bucket: str
    ev: float
    cohort: str
    channel: tuple[str, str]


def calibrate_settle_days(lag_days: list[float]) -> int:
    """p90 of (playback.last_updated_at - discovered_at) in days, rounded up to
    the week and clamped [14, 42]. last_updated_at is the LAST touch, not first,
    so this overestimates true first-touch lag — conservative (errs toward
    calling things PENDING rather than PASSED_OVER too early)."""
    if not lag_days:
        return 14
    lag_days = sorted(x for x in lag_days if x >= 0)
    if not lag_days:
        return 14
    idx = min(len(lag_days) - 1, max(0, math.ceil(0.9 * len(lag_days)) - 1))
    p90 = lag_days[idx]
    days = math.ceil(p90 / 7) * 7
    return int(max(14, min(42, days)))


def build_item_frame(
    src: dict[str, Any],
    doc_id: str,
    playback: dict[str, Any] | None,
    settle_days: int,
    now: datetime,
) -> ItemFrame:
    item_type = src.get("type") or "video"
    subscription_id = src.get("subscription_id") or ""
    author = (src.get("metadata") or {}).get("author")
    submission_source = src.get("submission_source")
    duration = src.get("duration_seconds")
    duration = duration if (duration and duration > 0) else None
    pos = 0.0
    if playback:
        pos = float(playback.get("position_seconds") or 0.0)
    wp = min(100.0, pos / duration * 100.0) if duration else None
    quarantined = bool(src.get("quarantined_at"))
    vote = src.get("user_interest")
    vote = vote if vote in ("up", "down") else None
    consumed = bool(src.get("consumed"))
    viewed = bool(src.get("viewed"))

    discovered_raw = src.get("discovered_at")
    discovered_at = _parse_dt(discovered_raw)
    age_days = (now - discovered_at).total_seconds() / 86400 if discovered_at else None
    settled = age_days is not None and age_days >= settle_days

    engagement_score = (src.get("engagement") or {}).get("score")
    interest_score = src.get("interest_score")

    if item_type == "article":
        state = classify_article_state(quarantined=quarantined, vote=vote, consumed=consumed, settled=settled)
    else:
        band = _watch_band(pos, wp)
        state = classify_av_state(
            quarantined=quarantined, vote=vote, band=band, consumed=consumed,
            viewed=viewed, discovered_at=discovered_at, settled=settled,
        )
    bucket = STATE_BUCKET[state]
    ev = item_ev(state, vote)
    cohort = assign_cohort(subscription_id, item_type, submission_source)
    ckey = channel_key(subscription_id, author)

    return ItemFrame(
        id=doc_id, type=item_type, title=src.get("title") or "", url=src.get("url") or "",
        subscription_id=subscription_id, author=author, submission_source=submission_source,
        discovered_at=discovered_at, duration=duration, position=pos, wp=wp,
        quarantined=quarantined, quarantine_source=src.get("quarantine_source"),
        vote=vote, consumed=consumed, viewed=viewed,
        engagement_score=engagement_score, interest_score=interest_score,
        settled=settled, state=state, bucket=bucket, ev=ev, cohort=cohort, channel=ckey,
    )


def _parse_dt(raw: Any) -> datetime | None:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    try:
        s = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ---- statistics ----


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), p, min(1.0, center + half))


def estimate_shrinkage_m(rates_and_ns: list[tuple[float, int]], p0: float) -> float:
    """Method-of-moments prior strength for Beta-Binomial shrinkage. Falls back
    to FALLBACK_SHRINKAGE_M with fewer than 5 qualifying channels."""
    qualifying = [(p, n) for p, n in rates_and_ns if n >= MIN_ELIGIBLE_FOR_LEADERBOARD]
    if len(qualifying) < 5:
        return FALLBACK_SHRINKAGE_M
    ps = np.array([p for p, _ in qualifying])
    ns = np.array([n for _, n in qualifying])
    weights = ns / ns.sum()
    var_obs = float(np.average((ps - p0) ** 2, weights=weights))
    var_bin = float(np.mean(p0 * (1 - p0) / ns))
    tau2 = max(var_obs - var_bin, 1e-6)
    if tau2 <= 1e-6 or p0 in (0.0, 1.0):
        return FALLBACK_SHRINKAGE_M
    m = p0 * (1 - p0) / tau2
    return float(np.clip(m, MIN_SHRINKAGE_M, MAX_SHRINKAGE_M))


def shrink_rate(k: int, n: int, p0: float, m: float) -> float:
    return (k + m * p0) / (n + m) if (n + m) > 0 else p0


def fisher_vs_baseline(k: int, n: int, k_base: int, n_base: int) -> tuple[float, float, float]:
    """Returns (p_fisher, risk_diff, lift) for channel (k/n) vs leave-one-out baseline."""
    if n == 0 or n_base <= 0:
        return (1.0, 0.0, 1.0)
    table = [[k, n - k], [k_base, n_base - k_base]]
    try:
        _, p_fisher = sstats.fisher_exact(table, alternative="two-sided")
    except ValueError:
        p_fisher = 1.0
    p_channel = k / n
    p_base = k_base / n_base if n_base else 0.0
    risk_diff = p_channel - p_base
    lift = (p_channel / p_base) if p_base > 0 else (float("inf") if p_channel > 0 else 1.0)
    return (float(p_fisher), float(risk_diff), float(lift))


def bh_adjust(p_values: list[float], q: float = BH_Q) -> list[bool]:
    """Benjamini-Hochberg: returns per-entry pass/fail at FDR q, in the input order."""
    n = len(p_values)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: p_values[i])
    passed = [False] * n
    max_rank_passed = -1
    for rank, idx in enumerate(order, start=1):
        if p_values[idx] <= (rank / n) * q:
            max_rank_passed = rank
    if max_rank_passed >= 0:
        for rank, idx in enumerate(order, start=1):
            if rank <= max_rank_passed:
                passed[idx] = True
    return passed


def significance_badge(bh_pass: bool, risk_diff: float, min_effect_pp: float = MIN_EFFECT_PP) -> str:
    if bh_pass and abs(risk_diff) >= min_effect_pp:
        return "above" if risk_diff > 0 else "below"
    return "typical"


def percentile_rank(sorted_values: list[float], value: float) -> float:
    n = len(sorted_values)
    if n == 0:
        return 50.0
    return round(bisect.bisect_left(sorted_values, value) / n * 100, 1)


def mann_whitney_vs_baseline(channel_values: list[float], baseline_values: list[float]) -> tuple[float | None, float | None]:
    """Returns (p_value, cliffs_delta) for channel watch-depth vs baseline, or
    (None, None) if either sample is too small to test."""
    if len(channel_values) < 2 or len(baseline_values) < 2:
        return (None, None)
    try:
        res = sstats.mannwhitneyu(channel_values, baseline_values, alternative="two-sided")
    except ValueError:
        return (None, None)
    u = float(res.statistic)
    delta = 2 * u / (len(channel_values) * len(baseline_values)) - 1
    return (float(res.pvalue), float(delta))


def cochran_armitage(successes: list[int], totals: list[int]) -> tuple[float | None, float | None]:
    """Trend test over an ordered sequence of (successes, totals) bins (e.g.
    months). Returns (z, two-sided p), or (None, None) if degenerate."""
    k = len(totals)
    n_total = sum(totals)
    r_total = sum(successes)
    if k < 2 or n_total == 0 or r_total == 0 or r_total == n_total:
        return (None, None)
    scores = list(range(k))
    r_over_n = r_total / n_total
    t_num = sum(t * (r - n * r_over_n) for t, r, n in zip(scores, successes, totals))
    sum_t2n = sum((t ** 2) * n for t, n in zip(scores, totals))
    sum_tn = sum(t * n for t, n in zip(scores, totals))
    var = r_over_n * (1 - r_over_n) * (sum_t2n - (sum_tn ** 2) / n_total)
    if var <= 0:
        return (None, None)
    z = t_num / math.sqrt(var)
    p = 2 * (1 - sstats.norm.cdf(abs(z)))
    return (float(z), float(p))


# ---- channel / cohort aggregation ----


@dataclass
class MetricPair:
    """A rate (k/n) with its raw value, shrunk value, Wilson CI, and
    significance-vs-baseline result, computed together since they share k/n."""
    k: int
    n: int
    raw: float
    shrunk: float
    wilson_low: float
    wilson_high: float
    risk_diff: float = 0.0
    lift: float = 1.0
    p_fisher: float = 1.0
    bh_pass: bool = False
    badge: str = "typical"
    percentile: float = 50.0


def _state_counts(items: list[ItemFrame]) -> dict[str, int]:
    c: Counter[str] = Counter(i.state for i in items)
    return {s: c.get(s, 0) for s in STATE_BUCKET}


def _bucket_counts(items: list[ItemFrame]) -> dict[str, int]:
    c: Counter[str] = Counter(i.bucket for i in items)
    return {b: c.get(b, 0) for b in ("positive", "leaning_positive", "leaning_negative", "negative", "excluded")}


def summarize_group(items: list[ItemFrame]) -> dict[str, Any]:
    eligible = [i for i in items if i.state != "PENDING" and not i.quarantined]
    n_eligible = len(eligible)
    n_positive = sum(1 for i in eligible if i.state in POSITIVE_STATES)
    n_negative = sum(1 for i in eligible if i.state in NEGATIVE_STATES)
    n_explicit_neg = sum(1 for i in eligible if i.state in EXPLICIT_NEGATIVE_STATES)
    n_upvote = sum(1 for i in eligible if i.vote == "up")
    wps = [i.wp for i in eligible if i.wp is not None]
    watch_sec = sum((min(i.position, i.duration) if i.duration else i.position) for i in items if i.type != "article")
    dur_known_items = [i for i in items if i.duration]
    runtime_sum = sum(min(i.position, i.duration) for i in dur_known_items)
    dur_sum = sum(i.duration for i in dur_known_items)
    return {
        "n_total": len(items),
        "n_eligible": n_eligible,
        "n_pending": sum(1 for i in items if i.state == "PENDING"),
        "n_quarantined": sum(1 for i in items if i.quarantined),
        "state_counts": _state_counts(items),
        "bucket_counts": _bucket_counts(items),
        "positive_k": n_positive,
        "negative_k": n_negative,
        "explicit_negative_k": n_explicit_neg,
        "positive_rate": (n_positive / n_eligible) if n_eligible else 0.0,
        "negative_rate": (n_negative / n_eligible) if n_eligible else 0.0,
        "explicit_neg_rate": (n_explicit_neg / n_eligible) if n_eligible else 0.0,
        "upvote_rate": (n_upvote / n_eligible) if n_eligible else 0.0,
        "mean_ev": (sum(i.ev for i in eligible) / n_eligible) if n_eligible else 0.0,
        "watched_minutes": watch_sec / 60.0,
        "runtime_share": (runtime_sum / dur_sum) if dur_sum else None,
        "median_wp": float(np.median(wps)) if wps else None,
        "wp_quartiles": _tukey_quartiles(wps),
        "n_dur_unknown": sum(1 for i in items if i.duration is None and i.type != "article"),
        "_wps": wps,
    }


def _tukey_quartiles(values: list[float]) -> dict[str, float] | None:
    """Pre-computed box-plot summary (Tukey fences) so the frontend can render
    a Plotly box trace from quartiles alone, without shipping per-item arrays."""
    if not values:
        return None
    q1, median, q3 = (float(x) for x in np.percentile(values, [25, 50, 75]))
    iqr = q3 - q1
    low_fence = max(min(values), q1 - 1.5 * iqr)
    high_fence = min(max(values), q3 + 1.5 * iqr)
    return {"q1": q1, "median": median, "q3": q3, "low_fence": low_fence, "high_fence": high_fence}


def _make_metric_pair(k: int, n: int, k_base: int, n_base: int, m: float, p0: float) -> MetricPair:
    low, raw, high = wilson_interval(k, n)
    shrunk = shrink_rate(k, n, p0, m)
    p_fisher, risk_diff, lift = fisher_vs_baseline(k, n, k_base, n_base)
    return MetricPair(k=k, n=n, raw=raw, shrunk=shrunk, wilson_low=low, wilson_high=high,
                       risk_diff=risk_diff, lift=lift, p_fisher=p_fisher)


def compute_channels(
    video_items: list[ItemFrame],
    subscriptions: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    by_channel: dict[tuple[str, str], list[ItemFrame]] = defaultdict(list)
    for it in video_items:
        by_channel[it.channel].append(it)

    subscription_names_norm = {normalize_author(s.get("name")): sid for sid, s in subscriptions.items()}

    all_eligible = [i for i in video_items if i.state != "PENDING" and not i.quarantined]
    n_all = len(all_eligible)
    k_all_pos = sum(1 for i in all_eligible if i.state in POSITIVE_STATES)
    k_all_neg = sum(1 for i in all_eligible if i.state in NEGATIVE_STATES)
    p0_pos = (k_all_pos / n_all) if n_all else 0.0
    p0_neg = (k_all_neg / n_all) if n_all else 0.0
    baseline_wps = sorted(i.wp for i in all_eligible if i.wp is not None)

    raw_rates_pos: list[tuple[float, int]] = []
    raw_rates_neg: list[tuple[float, int]] = []
    channel_rows: list[dict[str, Any]] = []

    for ckey, items in by_channel.items():
        if ckey[1] in ("\u0000unknown",):
            continue  # unknown-author bucket: counted in cohort totals, excluded from leaderboard
        summary = summarize_group(items)
        n_eligible = summary["n_eligible"]
        if n_eligible >= MIN_ELIGIBLE_FOR_LEADERBOARD:
            raw_rates_pos.append((summary["positive_rate"], n_eligible))
            raw_rates_neg.append((summary["negative_rate"], n_eligible))
        channel_rows.append({"key": ckey, "items": items, "summary": summary})

    m_pos = estimate_shrinkage_m(raw_rates_pos, p0_pos)
    m_neg = estimate_shrinkage_m(raw_rates_neg, p0_neg)

    qualifying_shrunk_pos: list[float] = []
    qualifying_shrunk_neg: list[float] = []

    results = []
    for row in channel_rows:
        ckey = row["key"]
        items = row["items"]
        summary = row["summary"]
        n_eligible = summary["n_eligible"]
        qualifies = n_eligible >= MIN_ELIGIBLE_FOR_LEADERBOARD

        k_pos, k_neg = summary["positive_k"], summary["negative_k"]
        n_base_pos, n_base_neg = n_all - n_eligible, n_all - n_eligible
        k_base_pos, k_base_neg = k_all_pos - k_pos, k_all_neg - k_neg

        pos_pair = _make_metric_pair(k_pos, n_eligible, k_base_pos, n_base_pos, m_pos, p0_pos)
        neg_pair = _make_metric_pair(k_neg, n_eligible, k_base_neg, n_base_neg, m_neg, p0_neg)

        cohort_counts = Counter(i.cohort for i in items)
        dominant_cohort = cohort_counts.most_common(1)[0][0] if cohort_counts else "adhoc_unclassified"
        is_mixed = len(cohort_counts) > 1

        if ckey[0] == "sub":
            sub = subscriptions.get(ckey[1], {})
            label = sub.get("name") or ckey[1]
            also_subscribed = False
        else:
            label = items[0].author or ckey[1]
            also_subscribed = normalize_author(label) in subscription_names_norm

        wps = summary.pop("_wps")
        mw_p, cliffs_delta = mann_whitney_vs_baseline(wps, baseline_wps) if wps else (None, None)
        wp_percentile = percentile_rank(baseline_wps, summary["median_wp"]) if summary["median_wp"] is not None else None

        scored = [i for i in items if i.engagement_score is not None]
        surprise = None
        if scored:
            mean_pred = sum(i.engagement_score for i in scored) / len(scored)
            surprise = pos_pair.shrunk - mean_pred

        if qualifies:
            qualifying_shrunk_pos.append(pos_pair.shrunk)
            qualifying_shrunk_neg.append(neg_pair.shrunk)

        results.append({
            "channel_id": f"{ckey[0]}:{ckey[1]}",
            "label": label,
            "kind": "subscription" if ckey[0] == "sub" else "adhoc",
            "dominant_cohort": dominant_cohort,
            "cohort_breakdown": dict(cohort_counts),
            "is_mixed_cohort": is_mixed,
            "also_subscribed": also_subscribed,
            "qualifies": qualifies,
            "n_total": summary["n_total"],
            "n_eligible": n_eligible,
            "n_quarantined": summary["n_quarantined"],
            "state_counts": summary["state_counts"],
            "bucket_counts": summary["bucket_counts"],
            "positive": pos_pair,
            "negative": neg_pair,
            "mean_ev": summary["mean_ev"],
            "watched_minutes": round(summary["watched_minutes"], 1),
            "runtime_share": summary["runtime_share"],
            "median_wp": summary["median_wp"],
            "watch_depth_percentile": wp_percentile,
            "cliffs_delta": cliffs_delta,
            "mannwhitney_p": mw_p,
            "n_dur_unknown": summary["n_dur_unknown"],
            "surprise": surprise,
        })

    qualifying_shrunk_pos.sort()
    qualifying_shrunk_neg.sort()
    p_fisher_pos_list = [r["positive"].p_fisher for r in results if r["qualifies"]]
    p_fisher_neg_list = [r["negative"].p_fisher for r in results if r["qualifies"]]
    bh_pos = bh_adjust(p_fisher_pos_list)
    bh_neg = bh_adjust(p_fisher_neg_list)
    bh_pos_iter = iter(bh_pos)
    bh_neg_iter = iter(bh_neg)

    for r in results:
        if r["qualifies"]:
            r["positive"].bh_pass = next(bh_pos_iter)
            r["negative"].bh_pass = next(bh_neg_iter)
            r["positive"].badge = significance_badge(r["positive"].bh_pass, r["positive"].risk_diff)
            r["negative"].badge = significance_badge(r["negative"].bh_pass, r["negative"].risk_diff)
            r["positive"].percentile = percentile_rank(qualifying_shrunk_pos, r["positive"].shrunk)
            r["negative"].percentile = percentile_rank(qualifying_shrunk_neg, r["negative"].shrunk)
        else:
            r["positive"].badge = "too_few"
            r["negative"].badge = "too_few"

    return results


# ---- decline metric: "voted up, but not watched here" ----


def _month_key(dt: datetime) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"


def compute_decline_series(
    video_items: list[ItemFrame], now: datetime, settle_days: int, broad: bool = False,
) -> dict[str, Any]:
    non_q = [i for i in video_items if not i.quarantined and i.discovered_at is not None]

    def month_of(i: ItemFrame) -> str:
        return _month_key(i.discovered_at)

    months = sorted({month_of(i) for i in non_q})
    # Drop the trailing month if it hasn't settled yet.
    settled_months = [
        m for m in months
        if (now - datetime.strptime(m, "%Y-%m").replace(tzinfo=timezone.utc)).days >= settle_days
    ]

    def is_mwe(i: ItemFrame, broad: bool) -> bool:
        if not (i.consumed and i.vote == "up"):
            return False
        if i.wp is None:
            return i.position < NEGLIGIBLE_MAX_SECONDS
        limit = SAMPLED_MAX_PCT if broad else NEGLIGIBLE_MAX_PCT
        return i.wp < limit or (not broad and i.position < NEGLIGIBLE_MAX_SECONDS)

    rows = []
    mwe_counts, upvote_denoms = [], []
    for m in settled_months:
        month_items = [i for i in non_q if month_of(i) == m]
        upvoted = [i for i in month_items if i.vote == "up"]
        consumed_items = [i for i in month_items if i.consumed]
        mwe = [i for i in month_items if is_mwe(i, broad=broad)]
        bookmarked = [i for i in month_items if i.state == "BOOKMARKED"]
        dismissed = [i for i in month_items if i.state == "DISMISSED"]

        n_up, n_cons = len(upvoted), len(consumed_items)
        rate_upvotes_low, rate_upvotes, rate_upvotes_high = wilson_interval(len(mwe), n_up) if n_up else (0, 0, 0)
        rate_consumed_low, rate_consumed, rate_consumed_high = wilson_interval(len(mwe), n_cons) if n_cons else (0, 0, 0)

        rows.append({
            "month": m,
            "n_upvoted": n_up,
            "n_consumed": n_cons,
            "count_mwe": len(mwe),
            "rate_of_upvotes": rate_upvotes,
            "rate_of_upvotes_low": rate_upvotes_low,
            "rate_of_upvotes_high": rate_upvotes_high,
            "rate_of_consumed": rate_consumed,
            "bookmarked_rate": (len(bookmarked) / len(month_items)) if month_items else 0.0,
            "dismissed_rate": (len(dismissed) / len(month_items)) if month_items else 0.0,
            "confident": n_up >= 10,
        })
        mwe_counts.append(len(mwe))
        upvote_denoms.append(n_up)

    ca_z, ca_p = cochran_armitage(mwe_counts, upvote_denoms)
    if len(rows) >= 3:
        rho, spearman_p = sstats.spearmanr(range(len(rows)), [r["rate_of_upvotes"] for r in rows])
        rho, spearman_p = float(rho), float(spearman_p)
    else:
        rho, spearman_p = None, None

    if ca_p is not None and ca_p < 0.05 and ca_z is not None:
        label = "declining" if ca_z < 0 else "rising"
    else:
        label = "no_clear_trend"

    return {
        "settle_days": settle_days,
        "months": rows,
        "trend": {
            "ca_z": ca_z, "ca_p": ca_p, "spearman_rho": rho, "spearman_p": spearman_p, "label": label,
        },
    }


# ---- prediction calibration ----


def compute_calibration(video_items: list[ItemFrame]) -> dict[str, Any]:
    scored = [i for i in video_items if i.engagement_score is not None and i.state != "PENDING" and not i.quarantined]
    if len(scored) < 20:
        return {"deciles": []}
    scored.sort(key=lambda i: i.engagement_score)
    n = len(scored)
    decile_size = max(1, n // 10)
    deciles = []
    for d in range(10):
        start, end = d * decile_size, (d + 1) * decile_size if d < 9 else n
        chunk = scored[start:end]
        if not chunk:
            continue
        n_pos = sum(1 for i in chunk if i.state in POSITIVE_STATES)
        deciles.append({
            "decile": d + 1,
            "mean_predicted": sum(i.engagement_score for i in chunk) / len(chunk),
            "observed_positive_rate": n_pos / len(chunk),
            "n": len(chunk),
        })
    return {"deciles": deciles}
