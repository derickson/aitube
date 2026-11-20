"""Mirror selected content from the prod cluster into the work cluster.

Prod (smaug) is the only instance that ingests. The passive work deployment reads
from its own Elasticsearch cluster, which this module keeps filled with the content
of subscriptions flagged ``mirror_to_work``. Only prod has the work cluster's
credentials (``WORK_ELASTICSEARCH_URL`` / ``WORK_ELASTICSEARCH_API_KEY``); when
they're unset everything here is a no-op.

Rather than dual-writing at each of the ~45 places that write content items, prod
re-syncs from its own cluster:

- ``sync_to_work(days=N)`` runs as the last phase of every poll. It upserts the
  flagged subscriptions' items discovered in the last N days, which covers changes
  that land after ingest (summary backfill and retries, categorization, engagement
  scores). It then makes the work cluster hold *exactly* the flagged set: work items
  whose subscription isn't flagged, and work items in the window that prod no longer
  has (deduplicated or deleted), are removed.
- ``sync_to_work(days=None)`` does the same over all time: the backfill when a
  subscription is flagged, and the repair path (``scripts/sync_to_work.py --full``).
- ``unmirror_subscription()`` purges a subscription when its flag is cleared.

Personal state never leaves prod (``EXCLUDED_FIELDS``), and upserts are partial
updates, so a field the work side might add later survives a re-sync.

Mirroring is best-effort: the poller logs a failure and carries on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from elasticsearch import AsyncElasticsearch
from elasticsearch.helpers import async_bulk, async_scan

from backend.app.config import settings
from backend.app.services.elasticsearch import (
    CATEGORY_CONFIG_INDEX,
    CONTENT_ITEMS_INDEX,
    INDEX_MAPPINGS,
    PLAYBACK_STATE_INDEX,
    SUBSCRIPTIONS_INDEX,
    get_es_client,
)

logger = logging.getLogger(__name__)

# Indices the work deployment reads. Its own app startup would create them too,
# but the mirror may run before the work container has ever started.
WORK_INDICES = (SUBSCRIPTIONS_INDEX, CONTENT_ITEMS_INDEX, CATEGORY_CONFIG_INDEX, PLAYBACK_STATE_INDEX)

# Fields that stay on prod: personal state, private notes, and pipeline bookkeeping
# that means nothing on a cluster that never ingests.
EXCLUDED_FIELDS = frozenset({
    "consumed",
    "viewed",
    "user_interest",
    "quarantined_at",
    "quarantine_reason_code",
    "quarantine_reason",
    "quarantine_source",
    "content_dlp_cache_id",
    "summary_error",
    "summary_error_code",
    "summary_failed_at",
    "transcript_attempts",
    "transcript_last_attempted_at",
})
EXCLUDED_SUBSCRIPTION_FIELDS = frozenset({"interest_notes", "last_polled_at"})

_CHUNK = 100

_client: AsyncElasticsearch | None = None


def is_configured() -> bool:
    return bool(settings.work_elasticsearch_url)


def get_work_client() -> AsyncElasticsearch | None:
    global _client
    if not is_configured():
        return None
    if _client is None:
        _client = AsyncElasticsearch(
            settings.work_elasticsearch_url,
            api_key=settings.work_elasticsearch_api_key or None,
            request_timeout=60,
        )
    return _client


async def close_work_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


@dataclass
class SyncResult:
    subscriptions: list[str] = field(default_factory=list)
    upserted: int = 0
    deleted: int = 0
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False

    def summary(self) -> str:
        mode = " (dry run)" if self.dry_run else ""
        return (
            f"work mirror{mode}: {len(self.subscriptions)} subscriptions, "
            f"{self.upserted} upserted, {self.deleted} deleted, {len(self.errors)} errors"
        )


def strip_item(source: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in source.items() if k not in EXCLUDED_FIELDS}


def strip_subscription(source: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in source.items() if k not in EXCLUDED_SUBSCRIPTION_FIELDS}


def items_query(sub_ids: list[str], days: int | None) -> dict[str, Any]:
    """Items of the given subscriptions, optionally limited to the last `days`."""
    filters: list[dict[str, Any]] = [{"terms": {"subscription_id": sub_ids}}]
    if days is not None:
        filters.append({"range": {"discovered_at": {"gte": f"now-{days}d"}}})
    return {"bool": {"filter": filters}}


async def ensure_work_indices(work: AsyncElasticsearch) -> None:
    for name in WORK_INDICES:
        if not await work.indices.exists(index=name):
            await work.indices.create(index=name, body=INDEX_MAPPINGS[name])
            logger.info("work mirror: created index %s", name)


async def flagged_subscriptions(es: AsyncElasticsearch) -> dict[str, dict[str, Any]]:
    resp = await es.search(
        index=SUBSCRIPTIONS_INDEX,
        body={"query": {"term": {"mirror_to_work": True}}, "size": 1000},
    )
    return {hit["_id"]: hit["_source"] for hit in resp["hits"]["hits"]}


async def _bulk(work: AsyncElasticsearch, actions: list[dict[str, Any]], result: SyncResult) -> int:
    if not actions:
        return 0
    ok, errors = await async_bulk(work, actions, chunk_size=_CHUNK, raise_on_error=False, stats_only=False)
    for err in errors[:5]:
        result.errors.append(str(err)[:300])
    return ok


async def sync_to_work(
    days: int | None = None,
    sub_ids: list[str] | None = None,
    dry_run: bool = False,
) -> SyncResult:
    """Make the work cluster match prod for flagged subscriptions.

    ``days=None`` syncs all time. ``sub_ids`` limits the sync to those flagged
    subscriptions and skips the global cleanup of unflagged ones.
    """
    result = SyncResult(dry_run=dry_run)
    work = get_work_client()
    if work is None:
        return result
    prod = get_es_client()

    flagged = await flagged_subscriptions(prod)
    if sub_ids is not None:
        flagged = {sid: src for sid, src in flagged.items() if sid in sub_ids}
    result.subscriptions = sorted(flagged)

    if not dry_run:
        await ensure_work_indices(work)

    # 1. Subscription docs (for source names in facets) and the category taxonomy.
    sub_actions = [
        {"_op_type": "index", "_index": SUBSCRIPTIONS_INDEX, "_id": sid, "_source": strip_subscription(src)}
        for sid, src in flagged.items()
    ]
    try:
        config = await prod.get(index=CATEGORY_CONFIG_INDEX, id="default")
        sub_actions.append(
            {"_op_type": "index", "_index": CATEGORY_CONFIG_INDEX, "_id": "default", "_source": config["_source"]}
        )
    except Exception:
        pass  # no saved taxonomy on prod; both sides fall back to the defaults
    if not dry_run:
        await _bulk(work, sub_actions, result)

    # 2. Items: upsert everything prod has in scope, remembering the ids.
    prod_ids: set[str] = set()
    if flagged:
        query = items_query(list(flagged), days)
        actions: list[dict[str, Any]] = []
        async for hit in async_scan(prod, index=CONTENT_ITEMS_INDEX, query={"query": query}, size=200):
            prod_ids.add(hit["_id"])
            actions.append({
                "_op_type": "update",
                "_index": CONTENT_ITEMS_INDEX,
                "_id": hit["_id"],
                "doc": strip_item(hit["_source"]),
                "doc_as_upsert": True,
            })
            if len(actions) >= _CHUNK:
                result.upserted += len(actions) if dry_run else await _bulk(work, actions, result)
                actions = []
        result.upserted += len(actions) if dry_run else await _bulk(work, actions, result)

    # 3. Deletions on work: items in scope that prod no longer has (dedup/deletes).
    stale: list[str] = []
    if flagged and await work.indices.exists(index=CONTENT_ITEMS_INDEX):
        async for hit in async_scan(
            work, index=CONTENT_ITEMS_INDEX, query={"query": items_query(list(flagged), days), "_source": False},
            size=1000,
        ):
            if hit["_id"] not in prod_ids:
                stale.append(hit["_id"])
    if stale:
        result.deleted += len(stale) if dry_run else await _bulk(
            work, [{"_op_type": "delete", "_index": CONTENT_ITEMS_INDEX, "_id": i} for i in stale], result
        )

    # 4. Global cleanup (not for a single-subscription sync): anything on work that
    #    belongs to a subscription that is no longer flagged.
    if sub_ids is None:
        result.deleted += await _purge_unflagged(work, list(flagged), dry_run)

    if not dry_run:
        await work.indices.refresh(index=CONTENT_ITEMS_INDEX)
    log = logger.warning if result.errors else logger.info
    log("%s", result.summary())
    for err in result.errors:
        logger.warning("work mirror error: %s", err)
    return result


async def _purge_unflagged(work: AsyncElasticsearch, keep_ids: list[str], dry_run: bool) -> int:
    deleted = 0
    for index, field_name in ((CONTENT_ITEMS_INDEX, "subscription_id"), (SUBSCRIPTIONS_INDEX, "_id")):
        if not await work.indices.exists(index=index):
            continue
        query = {"bool": {"must_not": [{"terms": {field_name: keep_ids}}]}} if keep_ids else {"match_all": {}}
        if dry_run:
            deleted += (await work.count(index=index, query=query))["count"] if index == CONTENT_ITEMS_INDEX else 0
            continue
        # Refresh first: docs re-indexed moments ago (step 1/2) would otherwise be seen
        # at their old version, and conflicts="proceed" would silently skip them.
        await work.indices.refresh(index=index)
        resp = await work.delete_by_query(index=index, query=query, conflicts="proceed", refresh=True)
        if index == CONTENT_ITEMS_INDEX:
            deleted += resp.get("deleted", 0)
    return deleted


async def mirror_subscription(sub_id: str) -> SyncResult:
    """Full backfill of one subscription (called when it gets flagged)."""
    return await sync_to_work(days=None, sub_ids=[sub_id])


async def unmirror_subscription(sub_id: str) -> int:
    """Remove a subscription and its items from the work cluster."""
    work = get_work_client()
    if work is None:
        return 0
    deleted = 0
    if await work.indices.exists(index=CONTENT_ITEMS_INDEX):
        await work.indices.refresh(index=CONTENT_ITEMS_INDEX)  # see _purge_unflagged
        resp = await work.delete_by_query(
            index=CONTENT_ITEMS_INDEX,
            query={"term": {"subscription_id": sub_id}},
            conflicts="proceed",
            refresh=True,
        )
        deleted = resp.get("deleted", 0)
    try:
        await work.delete(index=SUBSCRIPTIONS_INDEX, id=sub_id, refresh=True)
    except Exception:
        pass  # was never mirrored
    logger.info("work mirror: unmirrored subscription %s (%d items removed)", sub_id, deleted)
    return deleted
