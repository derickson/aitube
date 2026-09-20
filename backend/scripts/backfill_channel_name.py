"""Backfill `channel_name` on content items indexed before the field existed.

Joins each item's `subscription_id` to `aitube-subscriptions.name`. Ad-hoc
items (subscription_id="adhoc") have no subscription to join against and are
left alone — their channel_name is set at ingest time from feed/API metadata
when available.

    uv run python -m backend.scripts.backfill_channel_name --dry-run
    uv run python -m backend.scripts.backfill_channel_name
"""

import argparse
import asyncio
import logging
from typing import Any

from backend.app.services.elasticsearch import (
    CONTENT_ITEMS_INDEX,
    SUBSCRIPTIONS_INDEX,
    close_es_client,
    get_es_client,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

PAGE_SIZE = 500


async def _load_subscription_names(es) -> dict[str, str]:
    # Subscriptions is a small index (dozens, not thousands) — one bounded
    # fetch, no need for search_after pagination (which would require sorting
    # on _id, and Elastic Serverless disallows fielddata on that field).
    resp = await es.search(
        index=SUBSCRIPTIONS_INDEX,
        body={"size": 1000, "_source": ["name"], "query": {"match_all": {}}},
    )
    return {h["_id"]: h["_source"].get("name") or "" for h in resp["hits"]["hits"]}


async def _run(dry_run: bool) -> None:
    es = get_es_client()
    try:
        sub_names = await _load_subscription_names(es)
        logger.info("Loaded %d subscription names", len(sub_names))

        scanned = 0
        updated = 0
        skipped_adhoc = 0
        search_after = None
        while True:
            body: dict[str, Any] = {
                "size": PAGE_SIZE,
                "_source": ["subscription_id"],
                "query": {"bool": {"must_not": [{"exists": {"field": "channel_name"}}]}},
                "sort": [{"external_id": {"order": "asc", "missing": "_last"}}],
            }
            if search_after:
                body["search_after"] = search_after
            resp = await es.search(index=CONTENT_ITEMS_INDEX, body=body)
            hits = resp["hits"]["hits"]
            if not hits:
                break
            search_after = hits[-1].get("sort")

            actions: list[dict[str, Any]] = []
            for hit in hits:
                scanned += 1
                sub_id = hit["_source"].get("subscription_id")
                name = sub_names.get(sub_id)
                if not name:
                    skipped_adhoc += 1
                    continue
                updated += 1
                if dry_run:
                    logger.info("[dry-run] %s: channel_name=%r", hit["_id"], name)
                else:
                    actions.append({"update": {"_index": CONTENT_ITEMS_INDEX, "_id": hit["_id"]}})
                    actions.append({"doc": {"channel_name": name}})

            if actions:
                result = await es.bulk(operations=actions, refresh=False)
                if result.get("errors"):
                    for item in result["items"]:
                        err = item.get("update", {}).get("error")
                        if err:
                            logger.error("Bulk update failed: %s", err)

            if len(hits) < PAGE_SIZE:
                break

        logger.info(
            "Scanned %d, %s %d, skipped (no subscription match) %d",
            scanned, "would update" if dry_run else "updated", updated, skipped_adhoc,
        )
        if not dry_run:
            await es.indices.refresh(index=CONTENT_ITEMS_INDEX)
    finally:
        await close_es_client()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing them")
    args = parser.parse_args()
    asyncio.run(_run(args.dry_run))


if __name__ == "__main__":
    main()
