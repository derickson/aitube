"""Read-only API for the Topic Flow tab.

Reads the latest aitube-cluster-runs document for cluster metadata and the
content index for per-doc points (umap_x, umap_y, cluster_id).

NOTE: sorts use `external_id` (keyword) not `_id` — Elastic Serverless
disables fielddata on `_id`.
"""

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
