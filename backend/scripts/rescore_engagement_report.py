"""Compare the currently-deployed `aitube-engagement` model against the scores
already stored on previously-scored content items, and report where its
opinion changed.

This is a read-only "did the new model get better?" check for after a retrain
in aitube-prediction-model: it re-runs the pipeline's inference for each
already-scored doc via `_simulate` (no write), diffs the new prediction against
the stored one, and prints a text report of the items that flipped decision
(engaged <-> not_engaged) or moved P(engaged) by more than --threshold.

By default it makes no changes. Pass --apply to write the freshly computed
`engagement` back onto every doc this run touched (not just the flagged ones)
-- that's a deliberate opt-in, since the whole point of running this first is
to eyeball the report before anything gets overwritten.

Usage:
    uv run python -m backend.scripts.rescore_engagement_report
    uv run python -m backend.scripts.rescore_engagement_report --type video --threshold 0.15
    uv run python -m backend.scripts.rescore_engagement_report --limit 200      # quick sample
    uv run python -m backend.scripts.rescore_engagement_report --apply          # after reviewing, persist
    uv run python -m backend.scripts.rescore_engagement_report --output report.txt
"""

import argparse
import asyncio
from datetime import datetime, timezone
from typing import Any

from backend.app.services.elasticsearch import (
    CONTENT_ITEMS_INDEX,
    ENGAGEMENT_PIPELINE,
    SUBSCRIPTIONS_INDEX,
    get_es_client,
)

SEARCH_PAGE_SIZE = 500
SIMULATE_BATCH_SIZE = 100
BULK_BATCH_SIZE = 500


async def fetch_scored_docs(es, content_type: str | None, limit: int) -> list[dict[str, Any]]:
    """Page through every doc that already carries an engagement.score, via a
    point-in-time + search_after on _shard_doc (the standard deep-pagination
    approach -- sorting on _id directly needs fielddata, which is disabled).
    Returns the raw hits (id + _source fields we need)."""
    filters: list[dict] = [{"exists": {"field": "engagement.score"}}]
    if content_type:
        filters.append({"term": {"type": content_type}})
    query = {"bool": {"filter": filters}}

    pit = await es.open_point_in_time(index=CONTENT_ITEMS_INDEX, keep_alive="2m")
    pit_id = pit["id"]
    docs: list[dict[str, Any]] = []
    try:
        search_after = None
        while True:
            body: dict[str, Any] = {
                "query": query,
                "size": min(SEARCH_PAGE_SIZE, limit - len(docs)) if limit else SEARCH_PAGE_SIZE,
                "sort": [{"_shard_doc": "asc"}],
                "pit": {"id": pit_id, "keep_alive": "2m"},
                "_source": ["subscription_id", "type", "duration_seconds", "title", "url", "engagement"],
            }
            if search_after:
                body["search_after"] = search_after
            resp = await es.search(body=body)
            pit_id = resp.get("pit_id", pit_id)
            hits = resp["hits"]["hits"]
            if not hits:
                break
            docs.extend(hits)
            if limit and len(docs) >= limit:
                docs = docs[:limit]
                break
            search_after = hits[-1]["sort"]
    finally:
        try:
            await es.close_point_in_time(id=pit_id)
        except Exception:
            pass
    return docs


async def fetch_subscription_names(es) -> dict[str, str]:
    try:
        resp = await es.search(
            index=SUBSCRIPTIONS_INDEX,
            body={"size": 1000, "_source": ["name"]},
        )
        return {hit["_id"]: hit["_source"].get("name", "") for hit in resp["hits"]["hits"]}
    except Exception:
        return {}


async def simulate_new_scores(es, docs: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    """Run each doc's (subscription_id, type, duration_seconds) through the
    pipeline via _simulate. Returns {doc_id: new_engagement_or_None}. None
    means the simulate call errored for that doc (reported, not compared)."""
    results: dict[str, dict[str, Any] | None] = {}
    for start in range(0, len(docs), SIMULATE_BATCH_SIZE):
        batch = docs[start:start + SIMULATE_BATCH_SIZE]
        sim_docs = [
            {
                "_source": {
                    "subscription_id": d["_source"].get("subscription_id"),
                    "type": d["_source"].get("type"),
                    "duration_seconds": d["_source"].get("duration_seconds"),
                }
            }
            for d in batch
        ]
        resp = await es.ingest.simulate(id=ENGAGEMENT_PIPELINE, docs=sim_docs)
        for d, result in zip(batch, resp["docs"]):
            if "error" in result:
                results[d["_id"]] = None
            else:
                results[d["_id"]] = result["doc"]["_source"].get("engagement")
    return results


def build_report(
    docs: list[dict[str, Any]],
    new_scores: dict[str, dict[str, Any] | None],
    sub_names: dict[str, str],
    threshold: float,
    applied: bool,
) -> tuple[str, list[tuple[str, dict[str, Any]]]]:
    """Returns (report_text, [(doc_id, new_engagement)]) for every doc whose
    new score was successfully computed -- the apply step writes all of these,
    not just the flagged ones."""
    compared = []
    errors = 0
    for d in docs:
        old_eng = d["_source"].get("engagement") or {}
        new_eng = new_scores.get(d["_id"])
        if new_eng is None:
            errors += 1
            continue
        compared.append((d, old_eng, new_eng))

    flips_to_engaged = flips_to_not_engaged = 0
    significant = []
    deltas = []
    for d, old_eng, new_eng in compared:
        old_score = old_eng.get("score")
        new_score = new_eng.get("score")
        if old_score is None or new_score is None:
            continue
        delta = new_score - old_score
        deltas.append(delta)
        old_pred = old_eng.get("prediction")
        new_pred = new_eng.get("prediction")
        flipped = old_pred != new_pred
        if flipped:
            if new_pred == "engaged":
                flips_to_engaged += 1
            else:
                flips_to_not_engaged += 1
        if flipped or abs(delta) >= threshold:
            significant.append((d, old_eng, new_eng, delta, flipped))

    significant.sort(key=lambda row: -abs(row[3]))

    lines = []
    lines.append("AI Tube Engagement Rescore Report")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"Pipeline: {ENGAGEMENT_PIPELINE}")
    lines.append(f"Docs matched (previously scored): {len(docs)}")
    lines.append(f"Successfully re-simulated: {len(compared)}   Simulate errors: {errors}")
    lines.append(f"Mode: {'APPLIED (scores rewritten)' if applied else 'DRY RUN (no scores changed)'}")
    lines.append("")
    lines.append("=== Summary ===")
    n = len(deltas)
    if n:
        mean_abs = sum(abs(x) for x in deltas) / n
        max_abs = max(abs(x) for x in deltas)
    else:
        mean_abs = max_abs = 0.0
    lines.append(f"Decision flips: {flips_to_engaged + flips_to_not_engaged} ({(flips_to_engaged + flips_to_not_engaged) / n:.1%})" if n else "Decision flips: 0")
    lines.append(f"  not_engaged -> engaged: {flips_to_engaged}")
    lines.append(f"  engaged -> not_engaged: {flips_to_not_engaged}")
    lines.append(f"Significant score moves (|delta| >= {threshold}, no flip): "
                 f"{sum(1 for *_r, flipped in significant if not flipped)}")
    lines.append(f"Mean |delta|: {mean_abs:.4f}   Max |delta|: {max_abs:.4f}")
    lines.append(f"Unchanged / below threshold: {n - len(significant)}")
    lines.append("")
    lines.append(f"=== Flagged items ({len(significant)} total, sorted by |delta| desc) ===")
    for d, old_eng, new_eng, delta, flipped in significant:
        src = d["_source"]
        sub_name = sub_names.get(src.get("subscription_id", ""), src.get("subscription_id", "?"))
        tag = "FLIP " if flipped else "DELTA"
        old_pred, new_pred = old_eng.get("prediction"), new_eng.get("prediction")
        pred_str = f"{old_pred}->{new_pred}" if flipped else old_pred
        sign = "+" if delta >= 0 else ""
        lines.append(
            f"[{tag}] {old_eng.get('score', 0):.3f} -> {new_eng.get('score', 0):.3f} "
            f"(Δ{sign}{delta:.3f})  {pred_str:<24}  "
            f"\"{src.get('title', '')[:70]}\"  channel={sub_name}  id={d['_id']}"
        )
    if not significant:
        lines.append("(none)")

    to_write = [(d["_id"], new_eng) for d, _old, new_eng in compared]
    return "\n".join(lines), to_write


async def apply_updates(es, updates: list[tuple[str, dict[str, Any]]]) -> None:
    for start in range(0, len(updates), BULK_BATCH_SIZE):
        batch = updates[start:start + BULK_BATCH_SIZE]
        operations = []
        for doc_id, new_eng in batch:
            operations.append({"update": {"_index": CONTENT_ITEMS_INDEX, "_id": doc_id}})
            operations.append({"doc": {"engagement": new_eng}})
        resp = await es.bulk(operations=operations)
        if resp.get("errors"):
            first_error = next(
                (item["update"]["error"] for item in resp["items"] if "error" in item.get("update", {})),
                None,
            )
            print(f"  bulk apply: some updates failed, e.g. {first_error}")


async def main(content_type: str | None, threshold: float, limit: int, apply: bool, output: str | None) -> None:
    es = get_es_client()
    try:
        await es.ingest.get_pipeline(id=ENGAGEMENT_PIPELINE)
    except Exception as e:
        raise SystemExit(
            f"Pipeline '{ENGAGEMENT_PIPELINE}' not found in Elasticsearch: {e}\n"
            "Deploy it from the aitube-prediction-model project first "
            "(python scripts/import_to_elasticsearch.py)."
        )

    print(f"Fetching previously-scored docs (type={content_type or 'any'}, limit={limit or 'none'})...")
    docs = await fetch_scored_docs(es, content_type, limit)
    print(f"Found {len(docs)}. Fetching channel names...")
    sub_names = await fetch_subscription_names(es)
    print(f"Simulating new scores through '{ENGAGEMENT_PIPELINE}'...")
    new_scores = await simulate_new_scores(es, docs)

    report, updates = build_report(docs, new_scores, sub_names, threshold, applied=apply)
    print("\n" + report)
    if output:
        with open(output, "w") as f:
            f.write(report + "\n")
        print(f"\nReport written to {output}")

    if apply:
        print(f"\n--apply set: writing new engagement scores to {len(updates)} doc(s)...")
        await apply_updates(es, updates)
        print("Done.")
    else:
        print("\nDry run -- no documents were changed. Pass --apply to persist these scores.")

    await es.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--type", default=None, help="content type to compare, e.g. video or podcast_episode (default: all types)")
    parser.add_argument("--threshold", type=float, default=0.10, help="flag non-flip items whose P(engaged) moved by at least this much (default: 0.10)")
    parser.add_argument("--limit", type=int, default=0, help="cap the number of docs compared, for a quick sample (default: 0 = no limit)")
    parser.add_argument("--apply", action="store_true", help="write the newly computed scores back onto every compared doc (default: dry run, no writes)")
    parser.add_argument("--output", default=None, help="also write the text report to this file")
    args = parser.parse_args()
    asyncio.run(main(args.type, args.threshold, args.limit, args.apply, args.output))
