"""Backfill duration_seconds for videos missing it, using transcript chunks."""

import asyncio
import logging

from backend.app.services.elasticsearch import (
    CONTENT_ITEMS_INDEX,
    get_es_client,
    close_es_client,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def _run():
    es = get_es_client()

    # Find all videos with no duration
    resp = await es.search(
        index=CONTENT_ITEMS_INDEX,
        body={
            "query": {
                "bool": {
                    "must": [{"term": {"type": "video"}}],
                    "must_not": [{"exists": {"field": "duration_seconds"}}],
                }
            },
            "size": 500,
            "_source": ["title", "transcript"],
        },
    )

    # Also catch videos where duration_seconds is explicitly null/0
    resp2 = await es.search(
        index=CONTENT_ITEMS_INDEX,
        body={
            "query": {
                "bool": {
                    "must": [{"term": {"type": "video"}}],
                    "filter": [{"terms": {"duration_seconds": [0]}}],
                }
            },
            "size": 500,
            "_source": ["title", "transcript"],
        },
    )

    hits = {h["_id"]: h for h in resp["hits"]["hits"]}
    for h in resp2["hits"]["hits"]:
        hits[h["_id"]] = h

    logger.info("Found %d videos missing duration", len(hits))

    updated = 0
    skipped = 0
    for doc_id, hit in hits.items():
        source = hit["_source"]
        title = source.get("title", "")
        transcript = source.get("transcript")

        if not transcript or not isinstance(transcript, dict):
            logger.warning("  SKIP (no transcript): %s", title[:60])
            skipped += 1
            continue

        chunks = transcript.get("chunks", [])
        if not chunks:
            logger.warning("  SKIP (no chunks): %s", title[:60])
            skipped += 1
            continue

        last_end = chunks[-1].get("end", 0)
        if last_end <= 0:
            logger.warning("  SKIP (last chunk end=0): %s", title[:60])
            skipped += 1
            continue

        await es.update(
            index=CONTENT_ITEMS_INDEX,
            id=doc_id,
            doc={"duration_seconds": last_end},
        )
        logger.info("  UPDATED: %s -> %.0fs", title[:60], last_end)
        updated += 1

    logger.info("Done: %d updated, %d skipped", updated, skipped)


async def main():
    try:
        await _run()
    finally:
        await close_es_client()


if __name__ == "__main__":
    asyncio.run(main())
