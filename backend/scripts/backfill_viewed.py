"""Backfill the 'viewed' field for existing content items.

- All articles -> viewed: true
- Videos/podcasts with playback position > 0 -> viewed: true
- Everything else remains viewed: false (the default)
"""

import asyncio
import logging

from backend.app.services.elasticsearch import (
    CONTENT_ITEMS_INDEX,
    PLAYBACK_STATE_INDEX,
    get_es_client,
    close_es_client,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def _run():
    es = get_es_client()

    # Step 1: Mark all articles as viewed
    resp = await es.update_by_query(
        index=CONTENT_ITEMS_INDEX,
        body={
            "query": {"term": {"type": "article"}},
            "script": {"source": "ctx._source.viewed = true"},
        },
    )
    logger.info("Articles marked viewed: %d", resp.get("updated", 0))

    # Step 2: Find all playback states with position > 0
    playback_resp = await es.search(
        index=PLAYBACK_STATE_INDEX,
        body={
            "query": {"range": {"position_seconds": {"gt": 0}}},
            "size": 10000,
            "_source": ["content_item_id"],
        },
    )

    item_ids = list({
        hit["_source"]["content_item_id"]
        for hit in playback_resp["hits"]["hits"]
        if "content_item_id" in hit["_source"]
    })
    logger.info("Found %d content items with playback position > 0", len(item_ids))

    if item_ids:
        resp2 = await es.update_by_query(
            index=CONTENT_ITEMS_INDEX,
            body={
                "query": {"ids": {"values": item_ids}},
                "script": {"source": "ctx._source.viewed = true"},
            },
        )
        logger.info("Videos/podcasts marked viewed: %d", resp2.get("updated", 0))

    logger.info("Backfill complete.")


async def main():
    try:
        await _run()
    finally:
        await close_es_client()


if __name__ == "__main__":
    asyncio.run(main())
