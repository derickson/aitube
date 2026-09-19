"""Engagement analytics endpoint — backs the settings menu's Engagement page.

Fetches content items, playback state, and subscriptions from Elasticsearch,
then hands everything to backend.app.services.engagement_analysis for the
actual classification/statistics (kept separate so the analysis is unit
testable without ES). Scoped to type == "video" for full per-channel/
per-state treatment — see engagement_analysis module docstring.
"""

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from backend.app.services import engagement_analysis as ea
from backend.app.services.elasticsearch import (
    CONTENT_ITEMS_INDEX,
    PLAYBACK_STATE_INDEX,
    SUBSCRIPTIONS_INDEX,
    get_es_client,
)

router = APIRouter(prefix="/api/engagement", tags=["engagement"])

_SOURCE_FIELDS = [
    "type", "title", "url", "subscription_id", "submission_source", "metadata.author",
    "duration_seconds", "quarantined_at", "quarantine_source", "user_interest",
    "consumed", "viewed", "discovered_at", "engagement.score", "interest_score", "category",
]


class MetricPairOut(BaseModel):
    k: int
    n: int
    raw: float
    shrunk: float
    wilson_low: float
    wilson_high: float
    risk_diff: float
    lift: float
    p_fisher: float
    bh_pass: bool
    badge: str
    percentile: float


class ChannelOut(BaseModel):
    channel_id: str
    label: str
    kind: str
    dominant_cohort: str
    cohort_breakdown: dict[str, int]
    is_mixed_cohort: bool
    also_subscribed: bool
    qualifies: bool
    n_total: int
    n_eligible: int
    n_quarantined: int
    state_counts: dict[str, int]
    bucket_counts: dict[str, int]
    positive: MetricPairOut
    negative: MetricPairOut
    mean_ev: float
    watched_minutes: float
    runtime_share: float | None
    median_wp: float | None
    watch_depth_percentile: float | None
    cliffs_delta: float | None
    mannwhitney_p: float | None
    n_dur_unknown: int
    surprise: float | None


class WpQuartilesOut(BaseModel):
    q1: float
    median: float
    q3: float
    low_fence: float
    high_fence: float


class CohortSummaryOut(BaseModel):
    key: str
    label: str
    n_total: int
    n_eligible: int
    n_pending: int
    n_quarantined: int
    state_counts: dict[str, int]
    bucket_counts: dict[str, int]
    positive_rate: float
    negative_rate: float
    explicit_neg_rate: float
    upvote_rate: float
    mean_ev: float
    watched_minutes: float
    median_wp: float | None
    wp_quartiles: WpQuartilesOut | None


class DeclineMonthOut(BaseModel):
    month: str
    n_upvoted: int
    n_consumed: int
    count_mwe: int
    rate_of_upvotes: float
    rate_of_upvotes_low: float
    rate_of_upvotes_high: float
    rate_of_consumed: float
    bookmarked_rate: float
    dismissed_rate: float
    confident: bool


class TrendOut(BaseModel):
    ca_z: float | None
    ca_p: float | None
    spearman_rho: float | None
    spearman_p: float | None
    label: str


class DeclineOut(BaseModel):
    settle_days: int
    months: list[DeclineMonthOut]
    trend: TrendOut


class ConfusionMatrixOut(BaseModel):
    predicted_order: list[str]
    actual_order: list[str]
    counts: list[list[int]]


class CalibrationOut(BaseModel):
    matrix: ConfusionMatrixOut | None
    n: int
    accuracy: float | None = None
    precision: float | None = None
    recall: float | None = None


class CategoryQualityRowOut(BaseModel):
    category: str
    n: int
    accuracy: float | None = None
    precision: float | None = None
    recall: float | None = None


class CategoryQualityOut(BaseModel):
    rows: list[CategoryQualityRowOut]


class EngagementReportResponse(BaseModel):
    generated_at: str
    settle_days: int
    video_total: int
    podcast_total: int
    article_total: int
    quarantine_sources_seen: list[str]
    corpus_median_wp: float | None
    cohorts: list[CohortSummaryOut]
    channels: list[ChannelOut]
    decline_strict: DeclineOut
    decline_broad: DeclineOut
    calibration: CalibrationOut
    category_quality: CategoryQualityOut


@router.get("/report/", response_model=EngagementReportResponse)
async def engagement_report():
    es = get_es_client()
    now = datetime.now(timezone.utc)

    items_resp = await es.search(
        index=CONTENT_ITEMS_INDEX,
        body={"query": {"match_all": {}}, "_source": _SOURCE_FIELDS, "size": 10000},
    )
    hits = items_resp["hits"]["hits"]

    playback_resp = await es.search(
        index=PLAYBACK_STATE_INDEX,
        body={"query": {"match_all": {}}, "_source": ["content_item_id", "position_seconds", "last_updated_at"], "size": 10000},
    )
    playback_by_item: dict[str, dict[str, Any]] = {}
    for ph in playback_resp["hits"]["hits"]:
        src = ph["_source"]
        cid = src.get("content_item_id")
        pos = src.get("position_seconds") or 0
        if cid and pos >= (playback_by_item.get(cid, {}).get("position_seconds") or 0):
            playback_by_item[cid] = src

    subs_resp = await es.search(
        index=SUBSCRIPTIONS_INDEX,
        body={"query": {"match_all": {}}, "size": 1000},
    )
    subscriptions = {hit["_id"]: hit["_source"] for hit in subs_resp["hits"]["hits"]}

    # Calibrate SETTLE_DAYS from observed playback lag before classifying anything.
    lag_days = []
    for hit in hits:
        src = hit["_source"]
        discovered = ea._parse_dt(src.get("discovered_at"))
        pb = playback_by_item.get(hit["_id"])
        if discovered and pb:
            last_updated = ea._parse_dt(pb.get("last_updated_at"))
            if last_updated:
                lag_days.append((last_updated - discovered).total_seconds() / 86400)
    settle_days = ea.calibrate_settle_days(lag_days)

    frames = [
        ea.build_item_frame(hit["_source"], hit["_id"], playback_by_item.get(hit["_id"]), settle_days, now)
        for hit in hits
    ]

    video_items = [f for f in frames if f.type == "video"]
    podcast_total = sum(1 for f in frames if f.type == "podcast_episode")
    article_total = sum(1 for f in frames if f.type == "article")

    quarantine_sources_seen = sorted({f.quarantine_source for f in frames if f.quarantine_source})

    all_eligible_wps = sorted(
        f.wp for f in video_items if f.wp is not None and f.state != "PENDING" and not f.quarantined
    )
    corpus_median_wp = float(all_eligible_wps[len(all_eligible_wps) // 2]) if all_eligible_wps else None

    cohorts_out = []
    for key in ea.COHORT_ORDER:
        group = [f for f in video_items if f.cohort == key]
        summary = ea.summarize_group(group)
        summary.pop("_wps", None)
        wp_q = summary["wp_quartiles"]
        cohorts_out.append(CohortSummaryOut(
            key=key,
            label=ea.COHORT_LABELS[key],
            n_total=summary["n_total"],
            n_eligible=summary["n_eligible"],
            n_pending=summary["n_pending"],
            n_quarantined=summary["n_quarantined"],
            state_counts=summary["state_counts"],
            bucket_counts=summary["bucket_counts"],
            positive_rate=summary["positive_rate"],
            negative_rate=summary["negative_rate"],
            explicit_neg_rate=summary["explicit_neg_rate"],
            upvote_rate=summary["upvote_rate"],
            mean_ev=summary["mean_ev"],
            watched_minutes=round(summary["watched_minutes"], 1),
            median_wp=summary["median_wp"],
            wp_quartiles=WpQuartilesOut(**wp_q) if wp_q else None,
        ))

    channel_rows = ea.compute_channels(video_items, subscriptions)
    channels_out = [
        ChannelOut(
            channel_id=r["channel_id"], label=r["label"], kind=r["kind"],
            dominant_cohort=r["dominant_cohort"], cohort_breakdown=r["cohort_breakdown"],
            is_mixed_cohort=r["is_mixed_cohort"], also_subscribed=r["also_subscribed"],
            qualifies=r["qualifies"], n_total=r["n_total"], n_eligible=r["n_eligible"],
            n_quarantined=r["n_quarantined"], state_counts=r["state_counts"],
            bucket_counts=r["bucket_counts"],
            positive=MetricPairOut(**vars(r["positive"])),
            negative=MetricPairOut(**vars(r["negative"])),
            mean_ev=r["mean_ev"], watched_minutes=r["watched_minutes"],
            runtime_share=r["runtime_share"], median_wp=r["median_wp"],
            watch_depth_percentile=r["watch_depth_percentile"],
            cliffs_delta=r["cliffs_delta"], mannwhitney_p=r["mannwhitney_p"],
            n_dur_unknown=r["n_dur_unknown"], surprise=r["surprise"],
        )
        for r in channel_rows
    ]

    decline_strict = ea.compute_decline_series(video_items, now, settle_days, broad=False)
    decline_broad = ea.compute_decline_series(video_items, now, settle_days, broad=True)
    calibration = ea.compute_calibration(video_items)
    category_quality = ea.compute_category_quality(video_items)

    return EngagementReportResponse(
        generated_at=now.isoformat(),
        settle_days=settle_days,
        video_total=len(video_items),
        podcast_total=podcast_total,
        article_total=article_total,
        quarantine_sources_seen=quarantine_sources_seen,
        corpus_median_wp=corpus_median_wp,
        cohorts=cohorts_out,
        channels=channels_out,
        decline_strict=DeclineOut(**decline_strict),
        decline_broad=DeclineOut(**decline_broad),
        calibration=calibration,
        category_quality=category_quality,
    )

