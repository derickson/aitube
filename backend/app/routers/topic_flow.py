"""Read-only API for the Topic Flow tab.

Reads the latest aitube-cluster-runs document for cluster metadata and the
content index for per-doc points (umap_x, umap_y, cluster_id).

NOTE: sorts use `external_id` (keyword) not `_id` — Elastic Serverless
disables fielddata on `_id`.
"""

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.app.models.content import ContentItemSummary
from backend.app.services.elasticsearch import (
    CLUSTER_RUNS_INDEX,
    CONTENT_ITEMS_INDEX,
    get_es_client,
)

router = APIRouter(prefix="/api/topic-flow", tags=["topic-flow"])


class TopicFlowCluster(BaseModel):
    id: str
    label: str
    size: int
    top_terms: list[str]
    representative_item_ids: list[str] = []


class TopicFlowPoint(BaseModel):
    item_id: str
    cluster_id: str | None
    x: float
    y: float
    title: str
    type: str
    thumbnail_url: str | None
    published_at: str | None


class TopicFlowResponse(BaseModel):
    run_id: str
    created_at: str
    doc_count: int
    noise_count: int
    embedding_model: str
    lookback_days: int
    clusters: list[TopicFlowCluster]
    points: list[TopicFlowPoint]


class TopicFlowDailySeries(BaseModel):
    cluster_id: str
    label: str
    counts: list[int]


class TopicFlowOverTime(BaseModel):
    run_id: str
    days: list[str]
    series: list[TopicFlowDailySeries]


async def _load_latest_run() -> dict[str, Any] | None:
    es = get_es_client()
    try:
        resp = await es.search(
            index=CLUSTER_RUNS_INDEX,
            body={
                "size": 1,
                "sort": [{"created_at": {"order": "desc"}}],
                "query": {"match_all": {}},
            },
        )
    except Exception:
        return None
    hits = resp["hits"]["hits"]
    if not hits:
        return None
    return hits[0]["_source"]


async def _load_run_by_id(run_id: str) -> dict[str, Any] | None:
    es = get_es_client()
    try:
        resp = await es.get(index=CLUSTER_RUNS_INDEX, id=run_id)
    except Exception:
        return None
    return resp.get("_source")


async def _load_points(run_id: str) -> list[TopicFlowPoint]:
    es = get_es_client()
    points: list[TopicFlowPoint] = []
    search_after: list[Any] | None = None
    while True:
        body: dict[str, Any] = {
            "size": 1000,
            "_source": [
                "title", "type", "thumbnail_url", "published_at",
                "cluster_id", "umap_x", "umap_y",
            ],
            "query": {"term": {"cluster_run_id": run_id}},
            "sort": [{"external_id": {"order": "asc", "missing": "_last"}}],
        }
        if search_after:
            body["search_after"] = search_after
        resp = await es.search(index=CONTENT_ITEMS_INDEX, body=body)
        hits = resp["hits"]["hits"]
        if not hits:
            break
        for h in hits:
            s = h["_source"]
            if "umap_x" not in s or "umap_y" not in s:
                continue
            points.append(TopicFlowPoint(
                item_id=h["_id"],
                cluster_id=s.get("cluster_id"),
                x=float(s["umap_x"]),
                y=float(s["umap_y"]),
                title=s.get("title", "") or "",
                type=s.get("type", "") or "",
                thumbnail_url=s.get("thumbnail_url") or "",
                published_at=s.get("published_at"),
            ))
        search_after = hits[-1].get("sort")
        if len(hits) < 1000:
            break
    return points


@router.get("/latest/", response_model=TopicFlowResponse)
async def latest():
    run = await _load_latest_run()
    if not run:
        raise HTTPException(status_code=404, detail="No clustering run found. Run rebuild_clusters first.")
    points = await _load_points(run["run_id"])
    clusters = [
        TopicFlowCluster(
            id=c["id"],
            label=c.get("label", c["id"]),
            size=int(c.get("size", 0)),
            top_terms=c.get("top_terms", []),
            representative_item_ids=c.get("representative_item_ids", []),
        )
        for c in run.get("clusters", [])
    ]
    return TopicFlowResponse(
        run_id=run["run_id"],
        created_at=run["created_at"],
        doc_count=int(run.get("doc_count", 0)),
        noise_count=int(run.get("noise_count", 0)),
        embedding_model=run.get("embedding_model", ""),
        lookback_days=int(run.get("lookback_days", 30)),
        clusters=clusters,
        points=points,
    )


@router.get("/cluster/{cluster_id}/items/", response_model=list[ContentItemSummary])
async def cluster_items(cluster_id: str, run_id: str, size: int = 100):
    es = get_es_client()
    resp = await es.search(
        index=CONTENT_ITEMS_INDEX,
        body={
            "size": min(size, 500),
            "_source": [
                "subscription_id", "external_id", "type", "title", "url",
                "published_at", "discovered_at", "duration_seconds",
                "thumbnail_url", "summary", "interest_score",
                "user_interest", "consumed", "viewed",
            ],
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"cluster_run_id": run_id}},
                        {"term": {"cluster_id": cluster_id}},
                    ]
                }
            },
            "sort": [{"published_at": {"order": "desc", "missing": "_last"}}],
        },
    )
    return [
        ContentItemSummary(id=h["_id"], **h["_source"])
        for h in resp["hits"]["hits"]
    ]


@router.get("/flow/", response_model=TopicFlowOverTime)
async def flow(run_id: str | None = None):
    """Per-cluster daily content counts for the swimlane flow chart.

    Returns a continuous day axis (no interior gaps) and, for every non-noise
    cluster, a parallel `counts` array. Cluster order matches the run's cluster
    order (size desc) so colors line up with the scatter plot on the client.
    """
    if run_id:
        run = await _load_run_by_id(run_id) or await _load_latest_run()
    else:
        run = await _load_latest_run()
    if not run:
        raise HTTPException(status_code=404, detail="No clustering run found.")
    run_id = run["run_id"]

    label_by_id = {c["id"]: c.get("label", c["id"]) for c in run.get("clusters", [])}
    run_order = [c["id"] for c in run.get("clusters", [])]

    es = get_es_client()
    resp = await es.search(
        index=CONTENT_ITEMS_INDEX,
        body={
            "size": 0,
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"cluster_run_id": run_id}},
                        {"exists": {"field": "cluster_id"}},
                        {"exists": {"field": "published_at"}},
                    ]
                }
            },
            "aggs": {
                "clusters": {
                    "terms": {"field": "cluster_id", "size": 200},
                    "aggs": {
                        "by_day": {
                            "date_histogram": {
                                "field": "published_at",
                                "calendar_interval": "day",
                                "format": "yyyy-MM-dd",
                                "min_doc_count": 1,
                            }
                        }
                    },
                }
            },
        },
    )

    raw: dict[str, dict[str, int]] = {}
    all_days: set[str] = set()
    for bucket in resp.get("aggregations", {}).get("clusters", {}).get("buckets", []):
        cid = bucket["key"]
        daymap: dict[str, int] = {}
        for db in bucket["by_day"]["buckets"]:
            cnt = int(db["doc_count"])
            if cnt:
                daymap[db["key_as_string"]] = cnt
                all_days.add(db["key_as_string"])
        if daymap:
            raw[cid] = daymap

    if not all_days:
        return TopicFlowOverTime(run_id=run_id, days=[], series=[])

    sorted_days = sorted(all_days)
    start = datetime.strptime(sorted_days[0], "%Y-%m-%d")
    end = datetime.strptime(sorted_days[-1], "%Y-%m-%d")
    days: list[str] = []
    cursor = start
    while cursor <= end:
        days.append(cursor.strftime("%Y-%m-%d"))
        cursor += timedelta(days=1)

    # Run order first (matches scatter color assignment), then any stragglers.
    ordered = [c for c in run_order if c in raw] + [c for c in raw if c not in run_order]
    series = [
        TopicFlowDailySeries(
            cluster_id=cid,
            label=label_by_id.get(cid, cid),
            counts=[raw[cid].get(day, 0) for day in days],
        )
        for cid in ordered
    ]
    return TopicFlowOverTime(run_id=run_id, days=days, series=series)
