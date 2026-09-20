"""Category taxonomy settings + full-corpus recategorization.

The taxonomy Jev classifies content against (see content_classifier.py) is
editable here; changes take effect for classification going forward (new
ingest, ad-hoc add-content, the recurring backfill safety net). Existing
content items keep whatever category they were already given until a
recategorize sweep relabels them under the current taxonomy.

The recategorize sweep re-runs classify_content for every content item and
writes results back via the Bulk API's partial `doc` update, touching only
the `category` field — not a full-document reindex — so it doesn't churn
unrelated fields (in particular the semantic_text/copy_to fields on the v2
index, which would otherwise be re-embedded for no reason). It's a
full-corpus scan, so it runs as a detached background task; progress is
polled via the status endpoint.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.app.services import content_cache, content_classifier
from backend.app.services.content_classifier import classify_content
from backend.app.services.elasticsearch import CONTENT_ITEMS_INDEX, get_es_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/category", tags=["category"])

_PAGE_SIZE = 200
_CONCURRENCY = 5
_SOURCE_FIELDS = ["title", "summary", "metadata"]


# ---- settings ---------------------------------------------------------------


class CategoryIn(BaseModel):
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    label: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=1000)


class CategorySettingsIn(BaseModel):
    categories: list[CategoryIn]


class CategoryOut(BaseModel):
    slug: str
    label: str
    description: str


class CategorySettingsOut(BaseModel):
    categories: list[CategoryOut]


@router.get("/settings/", response_model=CategorySettingsOut)
async def get_category_settings():
    categories = await content_classifier.get_categories()
    return {"categories": categories}


@router.put("/settings/", response_model=CategorySettingsOut)
async def update_category_settings(body: CategorySettingsIn):
    if not body.categories:
        raise HTTPException(status_code=400, detail="At least one category is required")
    slugs = [c.slug for c in body.categories]
    if len(set(slugs)) != len(slugs):
        raise HTTPException(status_code=400, detail="Category slugs must be unique")

    await content_classifier.set_categories([c.model_dump() for c in body.categories])
    categories = await content_classifier.get_categories(force_refresh=True)
    return {"categories": categories}


# ---- recategorize all videos -------------------------------------------------

_recategorize_state: dict[str, Any] = {
    "status": "idle",  # idle | running | done | error
    "started_at": None,
    "finished_at": None,
    "total": None,
    "scanned": 0,
    "categorized": 0,
    "failed": 0,
    "error": None,
}
_recategorize_task: asyncio.Task | None = None


class RecategorizeStatus(BaseModel):
    status: str
    started_at: str | None
    finished_at: str | None
    total: int | None
    scanned: int
    categorized: int
    failed: int
    error: str | None


@router.post("/recategorize/")
async def recategorize_all():
    global _recategorize_task
    if _recategorize_state["status"] == "running":
        raise HTTPException(status_code=409, detail="Recategorization is already running")

    _recategorize_task = asyncio.create_task(_run_recategorize())
    return {"status": "accepted"}


@router.get("/recategorize/status/", response_model=RecategorizeStatus)
async def recategorize_status():
    return _recategorize_state


async def _classify_one(sem: asyncio.Semaphore, hit: dict[str, Any]) -> tuple[str, str | None, str | None]:
    """Returns (doc_id, category, error)."""
    src = hit["_source"]
    async with sem:
        category, error = await classify_content(
            title=src.get("title", ""),
            description=(src.get("metadata") or {}).get("description", ""),
            summary=src.get("summary", ""),
        )
    return hit["_id"], category, error


async def _run_recategorize() -> None:
    es = get_es_client()
    _recategorize_state.update(
        {
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "total": None,
            "scanned": 0,
            "categorized": 0,
            "failed": 0,
            "error": None,
        }
    )

    try:
        # Force this process onto the taxonomy as it stands right now, even if the
        # in-process cache hasn't expired yet (e.g. settings were just saved).
        await content_classifier.get_categories(force_refresh=True)

        count_resp = await es.count(index=CONTENT_ITEMS_INDEX, query={"match_all": {}})
        _recategorize_state["total"] = count_resp.get("count")

        sem = asyncio.Semaphore(_CONCURRENCY)
        pit = await es.open_point_in_time(index=CONTENT_ITEMS_INDEX, keep_alive="5m")
        pit_id = pit["id"]
        try:
            search_after = None
            while True:
                body: dict[str, Any] = {
                    "query": {"match_all": {}},
                    "size": _PAGE_SIZE,
                    "sort": [{"_shard_doc": "asc"}],
                    "pit": {"id": pit_id, "keep_alive": "5m"},
                    "_source": _SOURCE_FIELDS,
                }
                if search_after:
                    body["search_after"] = search_after

                resp = await es.search(body=body)
                hits = resp["hits"]["hits"]
                if not hits:
                    break
                pit_id = resp.get("pit_id", pit_id)
                search_after = hits[-1]["sort"]

                results = await asyncio.gather(*(_classify_one(sem, hit) for hit in hits))

                # Partial `doc` update via the bulk API — only the `category` field is
                # sent, so this doesn't reindex/re-vectorize any other field.
                actions: list[dict[str, Any]] = []
                for doc_id, category, error in results:
                    if category:
                        actions.append({"update": {"_index": CONTENT_ITEMS_INDEX, "_id": doc_id}})
                        actions.append({"doc": {"category": category}})
                        _recategorize_state["categorized"] += 1
                    else:
                        _recategorize_state["failed"] += 1
                        logger.warning("recategorize: could not classify %s: %s", doc_id, error)

                if actions:
                    result = await es.bulk(operations=actions, refresh=False)
                    if result.get("errors"):
                        for item in result["items"]:
                            err = item.get("update", {}).get("error")
                            if err:
                                logger.error("recategorize: bulk update failed: %s", err)

                _recategorize_state["scanned"] += len(hits)
        finally:
            await es.close_point_in_time(id=pit_id)

        if _recategorize_state["categorized"]:
            await es.indices.refresh(index=CONTENT_ITEMS_INDEX)
            content_cache.invalidate()

        _recategorize_state["status"] = "done"
    except Exception as e:
        logger.exception("recategorize_all failed")
        _recategorize_state["status"] = "error"
        _recategorize_state["error"] = str(e)
    finally:
        _recategorize_state["finished_at"] = datetime.now(timezone.utc).isoformat()
