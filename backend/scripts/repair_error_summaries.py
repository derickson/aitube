"""Repair content items whose stored `summary` is actually an API error payload.

`hermes -z` prints upstream API errors to stdout and exits 0, so before
hermes_client learned to recognise them the error text was stored verbatim as
the summary — e.g. "HTTP 404: The model `gpt-5.5` does not exist or you do not
have access to it." or "API call failed after 3 retries: HTTP 503: Service
Unavailable". Those items look summarized, so neither
`backfill_missing_summaries` (skips items that have a summary) nor
`retry_failed_summaries` (needs `summary_error` set) ever picks them up.

Detection uses `hermes_client.looks_like_error_response`, the same predicate that
now rejects these responses at ingest, so the two can't drift apart.

This finds them and regenerates the summary in place. Items that still can't be
summarized get `summary_error`/`summary_failed_at` set instead, so the normal
retry path can take over.

    uv run python -m backend.scripts.repair_error_summaries --dry-run
    uv run python -m backend.scripts.repair_error_summaries
"""

import argparse
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from backend.app.services.elasticsearch import (
    CONTENT_ITEMS_INDEX,
    close_es_client,
    get_es_client,
)
from backend.app.services.hermes_client import looks_like_error_response

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

PAGE_SIZE = 500

_SOURCE_FIELDS = [
    "title", "url", "type", "transcript", "content_markdown", "metadata",
    "summary", "summary_error", "discovered_at",
]


async def _find_poisoned(es) -> list[dict[str, Any]]:
    """Scan every content item for a summary that is really an error payload."""
    found: list[dict[str, Any]] = []
    pit = await es.open_point_in_time(index=CONTENT_ITEMS_INDEX, keep_alive="5m")
    pit_id = pit["id"]
    search_after = None
    scanned = 0
    try:
        while True:
            body: dict[str, Any] = {
                "query": {"match_all": {}},
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
            scanned += len(hits)
            for hit in hits:
                summary = (hit["_source"].get("summary") or "").strip()
                if not summary:
                    continue  # backfill_missing_summaries already covers these
                reason = looks_like_error_response(summary)
                if reason:
                    hit["_repair_reason"] = reason
                    found.append(hit)
    finally:
        await es.close_point_in_time(id=pit_id)
    logger.info("Scanned %d content items, found %d whose summary is not a usable summary", scanned, len(found))
    return found


async def _resummarize(es, hit: dict[str, Any]) -> bool:
    """Regenerate one item's summary. Returns True if a real summary was stored."""
    from backend.app.services.summarizer import summarize_content

    doc_id = hit["_id"]
    src = hit["_source"]
    title = src.get("title", "?")

    transcript = src.get("transcript")
    transcript_text = ""
    transcript_chunks = None
    if isinstance(transcript, dict):
        transcript_text = transcript.get("text", "")
        if transcript.get("chunks"):
            transcript_chunks = transcript["chunks"]

    source_text = transcript_text or src.get("content_markdown", "")
    metadata = src.get("metadata") or {}

    summary, summary_error = await summarize_content(
        title=title,
        content_type=src.get("type", "article"),
        transcript_text=source_text,
        description=metadata.get("description", ""),
        author=metadata.get("author", ""),
        transcript_chunks=transcript_chunks,
    )

    if summary:
        await es.update(
            index=CONTENT_ITEMS_INDEX,
            id=doc_id,
            body={"doc": {"summary": summary, "summary_error": None, "summary_failed_at": None}},
        )
        logger.info("Repaired '%s' (%s)", title[:60], doc_id)
        return True

    # Couldn't summarize now — clear the bogus summary and record the failure so
    # retry_failed_summaries/backfill_missing_summaries can pick it up later.
    await es.update(
        index=CONTENT_ITEMS_INDEX,
        id=doc_id,
        body={"doc": {
            "summary": "",
            "summary_error": (summary_error or src.get("summary") or "unknown")[:500],
            "summary_failed_at": datetime.now(timezone.utc).isoformat(),
        }},
    )
    logger.warning("Could not resummarize '%s' (%s): %s — queued for retry", title[:60], doc_id, summary_error)
    return False


async def _run(dry_run: bool, limit: int | None) -> None:
    es = get_es_client()
    try:
        poisoned = await _find_poisoned(es)
        if limit:
            poisoned = poisoned[:limit]

        if dry_run:
            for hit in poisoned:
                src = hit["_source"]
                logger.info(
                    "[dry-run] %s | %s | %s | %s\n             reason: %s",
                    hit["_id"], src.get("discovered_at"), src.get("type"),
                    (src.get("title") or "")[:60], hit.get("_repair_reason", "")[:120],
                )
            logger.info("[dry-run] would repair %d item(s)", len(poisoned))
            return

        repaired = 0
        for hit in poisoned:
            try:
                if await _resummarize(es, hit):
                    repaired += 1
            except Exception as e:
                logger.warning("Repair failed for %s: %s", hit["_id"], e)

        if poisoned:
            await es.indices.refresh(index=CONTENT_ITEMS_INDEX)
        logger.info("Repaired %d of %d item(s)", repaired, len(poisoned))
    finally:
        await close_es_client()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="List affected items without writing")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N items")
    args = parser.parse_args()
    asyncio.run(_run(args.dry_run, args.limit))


if __name__ == "__main__":
    main()
