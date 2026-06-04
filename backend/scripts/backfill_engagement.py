"""Materialize ML engagement predictions on existing content items.

Runs the `aitube-engagement` ingest pipeline (custom classifier loaded from the
aitube-prediction-model project) over already-indexed videos via update_by_query,
so the `engagement` field is populated without re-ingesting.

By default it only reprocesses unwatched videos (the watchlist the Prediction tab
shows). Pass --all to score every video, or --type to target another content type.

Usage:
    uv run python -m backend.scripts.backfill_engagement            # unwatched videos
    uv run python -m backend.scripts.backfill_engagement --all      # all videos
    uv run python -m backend.scripts.backfill_engagement --type podcast_episode
"""

import argparse
import asyncio

from backend.app.services.elasticsearch import (
    CONTENT_ITEMS_INDEX,
    ENGAGEMENT_PIPELINE,
    get_es_client,
)


async def main(content_type: str, only_unwatched: bool) -> None:
    es = get_es_client()

    # Confirm the pipeline is deployed before touching documents.
    try:
        await es.ingest.get_pipeline(id=ENGAGEMENT_PIPELINE)
    except Exception as e:
        raise SystemExit(
            f"Pipeline '{ENGAGEMENT_PIPELINE}' not found in Elasticsearch: {e}\n"
            "Deploy it from the aitube-prediction-model project first "
            "(python scripts/import_to_elasticsearch.py)."
        )

    filters: list[dict] = [{"term": {"type": content_type}}]
    if only_unwatched:
        filters.append(
            {
                "bool": {
                    "should": [
                        {"term": {"consumed": False}},
                        {"bool": {"must_not": {"exists": {"field": "consumed"}}}},
                    ],
                    "minimum_should_match": 1,
                }
            }
        )
    query = {"bool": {"filter": filters}}

    total = (await es.count(index=CONTENT_ITEMS_INDEX, query=query))["count"]
    scope = "unwatched " if only_unwatched else ""
    print(f"Reprocessing {total} {scope}{content_type} item(s) through '{ENGAGEMENT_PIPELINE}'...")
    if total == 0:
        await es.close()
        return

    resp = await es.update_by_query(
        index=CONTENT_ITEMS_INDEX,
        pipeline=ENGAGEMENT_PIPELINE,
        conflicts="proceed",
        refresh=True,
        wait_for_completion=True,
        body={"query": query},
    )
    print(
        f"Done. updated={resp.get('updated')} "
        f"version_conflicts={resp.get('version_conflicts')} "
        f"failures={len(resp.get('failures', []))}"
    )
    if resp.get("failures"):
        print("First failure:", resp["failures"][0])

    scored = (
        await es.count(
            index=CONTENT_ITEMS_INDEX,
            query={"bool": {"filter": filters + [{"exists": {"field": "engagement.score"}}]}},
        )
    )["count"]
    print(f"{scored}/{total} now have engagement.score materialized.")
    await es.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--type", default="video", help="content type to reprocess (default: video)")
    parser.add_argument("--all", action="store_true", help="include watched items, not just unwatched")
    args = parser.parse_args()
    asyncio.run(main(content_type=args.type, only_unwatched=not args.all))
