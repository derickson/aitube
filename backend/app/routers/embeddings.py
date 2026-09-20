"""Read-only API for the Embeddings 3D view.

Reads the same latest `aitube-cluster-runs` document as the Topic Flow tab
(see `topic_flow.py`), but returns 3D coordinates (`embedding3d_x/y/z`,
computed alongside the 2D UMAP projection in `clustering.rebuild_clusters`)
plus the categorical fields the client can color points by.

Only one embedding source exists today (`clustering_vector`), but `points`
carry a `coords` dict keyed by embedding source so a second embedding can be
added later without changing this response shape — the client just gets
another key to offer in its "position by" dropdown.
"""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.app.services.clustering import load_latest_run
from backend.app.services.elasticsearch import CONTENT_ITEMS_INDEX, get_es_client

router = APIRouter(prefix="/api/embeddings", tags=["embeddings"])

# key -> display label. Extend this (and the coords lookup in `_load_points`)
# when a second embedding type is added.
_EMBEDDING_SOURCES: list[tuple[str, str]] = [
    ("clustering", "Topic Clustering (Jina)"),
]


class EmbeddingSource(BaseModel):
    key: str
    label: str


class EmbeddingCluster(BaseModel):
    id: str
    label: str
    size: int


class EmbeddingPoint(BaseModel):
    item_id: str
    coords: dict[str, tuple[float, float, float]]
    title: str
    type: str
    thumbnail_url: str | None
    published_at: str | None
    subscription_id: str
    channel_name: str | None
    category: str | None
    cluster_id: str | None
    cluster_label: str | None
    user_interest: str | None
    consumed: bool
    viewed: bool
    prediction: str | None
    prediction_score: float | None


class EmbeddingsResponse(BaseModel):
    run_id: str
    created_at: str
    doc_count: int
    embeddings: list[EmbeddingSource]
    clusters: list[EmbeddingCluster]
    points: list[EmbeddingPoint]


async def _load_points(run_id: str, label_by_cluster: dict[str, str]) -> list[EmbeddingPoint]:
    es = get_es_client()
    points: list[EmbeddingPoint] = []
    search_after: list[Any] | None = None
    fields = [
        "title", "type", "thumbnail_url", "published_at",
        "subscription_id", "channel_name", "category",
        "cluster_id", "user_interest", "consumed", "viewed",
        "engagement", "embedding3d_x", "embedding3d_y", "embedding3d_z",
    ]
    while True:
        body: dict[str, Any] = {
            "size": 1000,
            "_source": fields,
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
            if "embedding3d_x" not in s or "embedding3d_y" not in s or "embedding3d_z" not in s:
                continue
            engagement = s.get("engagement") or {}
            cluster_id = s.get("cluster_id")
            points.append(EmbeddingPoint(
                item_id=h["_id"],
                coords={
                    "clustering": (
                        float(s["embedding3d_x"]),
                        float(s["embedding3d_y"]),
                        float(s["embedding3d_z"]),
                    ),
                },
                title=s.get("title", "") or "",
                type=s.get("type", "") or "",
                thumbnail_url=s.get("thumbnail_url") or "",
                published_at=s.get("published_at"),
                subscription_id=s.get("subscription_id", "") or "",
                channel_name=s.get("channel_name"),
                category=s.get("category"),
                cluster_id=cluster_id,
                cluster_label=label_by_cluster.get(cluster_id) if cluster_id else None,
                user_interest=s.get("user_interest"),
                consumed=bool(s.get("consumed")),
                viewed=bool(s.get("viewed")),
                prediction=engagement.get("prediction"),
                prediction_score=engagement.get("score"),
            ))
        search_after = hits[-1].get("sort")
        if len(hits) < 1000:
            break
    return points


@router.get("/latest/", response_model=EmbeddingsResponse)
async def latest():
    run = await load_latest_run()
    if not run:
        raise HTTPException(status_code=404, detail="No clustering run found. Run rebuild_clusters first.")

    clusters = [
        EmbeddingCluster(id=c["id"], label=c.get("label", c["id"]), size=int(c.get("size", 0)))
        for c in run.get("clusters", [])
    ]
    label_by_cluster = {c.id: c.label for c in clusters}
    points = await _load_points(run["run_id"], label_by_cluster)

    return EmbeddingsResponse(
        run_id=run["run_id"],
        created_at=run["created_at"],
        doc_count=len(points),
        embeddings=[EmbeddingSource(key=k, label=v) for k, v in _EMBEDDING_SOURCES],
        clusters=clusters,
        points=points,
    )
