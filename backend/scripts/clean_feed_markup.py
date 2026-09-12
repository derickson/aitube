"""Strip leftover feed markup (CDATA wrappers, HTML tags, entities) from
already-indexed documents.

Feed titles wrapped in `<![CDATA[...]]>` were stored verbatim before the
sanitizer in `services/feed_text.py` existed, and podcast descriptions kept
raw HTML entities (`&#8211;`, `&amp;`). This rewrites those fields in place.

    uv run python -m backend.scripts.clean_feed_markup --dry-run
    uv run python -m backend.scripts.clean_feed_markup
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
from backend.app.services.feed_text import clean_feed_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

PAGE_SIZE = 500


def _changed(original: str, cleaned: str) -> bool:
    """True only when cleaning removed real markup, not just whitespace.

    Re-writing thousands of docs for a collapsed blank line isn't worth it.
    """
    if not cleaned:
        return False
    return " ".join(original.split()) != " ".join(cleaned.split())


def _content_item_fixes(source: dict[str, Any]) -> dict[str, Any]:
    """Return the subset of fields whose cleaned value differs from what's stored."""
    fixes: dict[str, Any] = {}

    title = source.get("title") or ""
    cleaned_title = clean_feed_text(title)
    if _changed(title, cleaned_title):
        fixes["title"] = cleaned_title

    metadata = source.get("metadata") or {}
    meta_fixes: dict[str, Any] = {}
    for field in ("description", "author"):
        value = metadata.get(field)
        if not isinstance(value, str) or not value:
            continue
        cleaned = clean_feed_text(value)
        if _changed(value, cleaned):
            meta_fixes[field] = cleaned
    if meta_fixes:
        fixes["metadata"] = meta_fixes

    return fixes


def _subscription_fixes(source: dict[str, Any]) -> dict[str, Any]:
    fixes: dict[str, Any] = {}
    for field in ("name", "description"):
        value = source.get(field)
        if not isinstance(value, str) or not value:
            continue
        cleaned = clean_feed_text(value)
        if _changed(value, cleaned):
            fixes[field] = cleaned
    return fixes


async def _clean_index(es, index: str, fixer, dry_run: bool) -> tuple[int, int]:
    """Scan an index, applying `fixer` to each doc. Returns (scanned, updated)."""
    scanned = 0
    updated = 0

    # PIT + search_after: _id can't be sorted on, and the shard doc order is
    # stable for the life of the point-in-time.
    pit = await es.open_point_in_time(index=index, keep_alive="5m")
    pit_id = pit["id"]
    search_after = None
    try:
        while True:
            body: dict[str, Any] = {
                "query": {"match_all": {}},
                "size": PAGE_SIZE,
                "sort": [{"_shard_doc": "asc"}],
                "pit": {"id": pit_id, "keep_alive": "5m"},
            }
            if search_after:
                body["search_after"] = search_after

            resp = await es.search(body=body)
            hits = resp["hits"]["hits"]
            if not hits:
                break
            pit_id = resp.get("pit_id", pit_id)
            search_after = hits[-1]["sort"]

            actions: list[dict[str, Any]] = []
            for hit in hits:
                scanned += 1
                fixes = fixer(hit["_source"])
                if not fixes:
                    continue
                updated += 1
                if dry_run:
                    logger.info(
                        "[dry-run] %s: %s",
                        hit["_id"],
                        {k: (v if not isinstance(v, str) else v[:100]) for k, v in fixes.items()},
                    )
                else:
                    actions.append({"update": {"_index": index, "_id": hit["_id"]}})
                    actions.append({"doc": fixes})

            if actions:
                result = await es.bulk(operations=actions, refresh=False)
                if result.get("errors"):
                    for item in result["items"]:
                        err = item.get("update", {}).get("error")
                        if err:
                            logger.error("Bulk update failed: %s", err)
    finally:
        await es.close_point_in_time(id=pit_id)

    return scanned, updated


async def _run(dry_run: bool) -> None:
    es = get_es_client()
    try:
        scanned, updated = await _clean_index(es, CONTENT_ITEMS_INDEX, _content_item_fixes, dry_run)
        logger.info("Content items: scanned %d, %s %d", scanned, "would update" if dry_run else "updated", updated)

        scanned, updated = await _clean_index(es, SUBSCRIPTIONS_INDEX, _subscription_fixes, dry_run)
        logger.info("Subscriptions: scanned %d, %s %d", scanned, "would update" if dry_run else "updated", updated)

        if not dry_run:
            await es.indices.refresh(index=CONTENT_ITEMS_INDEX)
            await es.indices.refresh(index=SUBSCRIPTIONS_INDEX)
    finally:
        await close_es_client()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing them")
    args = parser.parse_args()
    asyncio.run(_run(args.dry_run))


if __name__ == "__main__":
    main()
