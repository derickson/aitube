"""One-time backfill: categorize every existing content item that has no
`category` yet, via OpenRouter's Jev decisions model (see
backend/app/services/content_classifier.py).

Ongoing content gets categorized at ingest (feed_poller / add_content), with a
bounded recent-items safety net in feed_poller.backfill_missing_categories for
anything that slipped through. This script is for the one-time full-corpus
sweep after the feature was added, or for re-running after a taxonomy change
(pass --force to recategorize items that already have a category).

    uv run python -m backend.scripts.backfill_categories --dry-run
    uv run python -m backend.scripts.backfill_categories
    uv run python -m backend.scripts.backfill_categories --force --limit 50
"""

import argparse
import asyncio
import logging
from typing import Any

from backend.app.services.content_classifier import classify_content
from backend.app.services.elasticsearch import (
    CONTENT_ITEMS_INDEX,
    close_es_client,
    get_es_client,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

PAGE_SIZE = 200
_CONCURRENCY = 5
_SOURCE_FIELDS = ["title", "summary", "metadata", "category"]


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


async def _run(dry_run: bool, force: bool, limit: int | None) -> None:
    es = get_es_client()
    try:
        query: dict[str, Any] = {"match_all": {}}
        if not force:
            query = {"bool": {"must_not": [{"exists": {"field": "category"}}]}}

        sem = asyncio.Semaphore(_CONCURRENCY)
        scanned = 0
        categorized = 0
        failed = 0
        stop = False

        pit = await es.open_point_in_time(index=CONTENT_ITEMS_INDEX, keep_alive="5m")
        pit_id = pit["id"]
        search_after = None
        try:
            while not stop:
                body: dict[str, Any] = {
                    "query": query,
                    "size": PAGE_SIZE,
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

                if limit is not None:
                    remaining = limit - scanned
                    if remaining <= 0:
                        break
                    hits = hits[:remaining]

                scanned += len(hits)

                results = await asyncio.gather(*(_classify_one(sem, hit) for hit in hits))

                actions: list[dict[str, Any]] = []
                for doc_id, category, error in results:
                    title = next(h["_source"].get("title", "?") for h in hits if h["_id"] == doc_id)
                    if category:
                        categorized += 1
                        if dry_run:
                            logger.info("[dry-run] %s: '%s' -> %s", doc_id, title[:60], category)
                        else:
                            actions.append({"update": {"_index": CONTENT_ITEMS_INDEX, "_id": doc_id}})
                            actions.append({"doc": {"category": category}})
                    else:
                        failed += 1
                        logger.warning("Could not categorize '%s' (%s): %s", title[:60], doc_id, error)

                if actions:
                    result = await es.bulk(operations=actions, refresh=False)
                    if result.get("errors"):
                        for item in result["items"]:
                            err = item.get("update", {}).get("error")
                            if err:
                                logger.error("Bulk update failed: %s", err)

                logger.info(
                    "Progress: scanned %d, categorized %d, failed %d",
                    scanned, categorized, failed,
                )

                if limit is not None and scanned >= limit:
                    stop = True
        finally:
            await es.close_point_in_time(id=pit_id)

        if not dry_run and categorized:
            await es.indices.refresh(index=CONTENT_ITEMS_INDEX)

        logger.info(
            "Done: scanned %d, %s %d, failed to categorize %d",
            scanned, "would categorize" if dry_run else "categorized", categorized, failed,
        )
    finally:
        await close_es_client()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    parser.add_argument("--force", action="store_true", help="Recategorize items that already have a category")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N candidate items")
    args = parser.parse_args()
    asyncio.run(_run(args.dry_run, args.force, args.limit))


if __name__ == "__main__":
    main()
