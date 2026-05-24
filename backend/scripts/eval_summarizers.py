"""Offline batch eval: Haiku vs Hermes (GPT-5.4 mini) on real content items.

Samples recent items that have a usable transcript/source, summarizes each with BOTH
engines, scores them with a neutral LLM judge, writes every record to the
aitube-summary-evals index + a local JSONL, and prints an aggregate report.

Usage:
    uv run python -m backend.scripts.eval_summarizers --n 30 --types video,podcast_episode,article
    uv run python -m backend.scripts.eval_summarizers --n 10 --out evals/run.jsonl
"""

import argparse
import asyncio
import json
import logging
import statistics
from datetime import datetime, timezone
from pathlib import Path

from backend.app.config import settings
from backend.app.services.elasticsearch import (
    CONTENT_ITEMS_INDEX,
    SUMMARY_EVAL_INDEX,
    close_es_client,
    ensure_indices,
    get_es_client,
)
from backend.app.services.summary_eval import _source_from_item, compare_engines

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("elastic_transport.transport").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def _sample_items(types: list[str], n: int) -> list[dict]:
    """Fetch recent items of the given types that have a non-empty source to summarize."""
    es = get_es_client()
    resp = await es.search(
        index=CONTENT_ITEMS_INDEX,
        query={"terms": {"type": types}},
        sort=[{"discovered_at": {"order": "desc"}}],
        size=n * 4,  # over-fetch; many may lack a usable transcript/source
    )
    items: list[dict] = []
    for hit in resp["hits"]["hits"]:
        doc = {**hit["_source"], "_id": hit["_id"]}
        source_text, _ = _source_from_item(doc)
        if source_text.strip():
            items.append(doc)
        if len(items) >= n:
            break
    return items


def _mean_scores(records: list[dict], engine: str) -> dict[str, float]:
    dims = ["faithfulness", "specificity", "format", "conciseness"]
    out: dict[str, float] = {}
    for d in dims:
        vals = [
            r["judge"]["scores"][engine][d]
            for r in records
            if r.get("judge") and r["judge"]["scores"].get(engine) and r["judge"]["scores"][engine].get(d) is not None
        ]
        if vals:
            out[d] = round(statistics.mean(vals), 2)
    return out


def _pct(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    return round(s[min(len(s) - 1, int(p * len(s)))], 1)


def _print_report(records: list[dict]) -> None:
    n = len(records)
    judged = [r for r in records if r.get("judge")]
    wins = {"hermes": 0, "haiku": 0, "tie": 0}
    for r in judged:
        wins[r["judge"]["winner"]] = wins.get(r["judge"]["winner"], 0) + 1

    hermes_fail = sum(1 for r in records if not r["hermes"]["summary"])
    haiku_fail = sum(1 for r in records if not r["haiku"]["summary"])
    hermes_fmt = sum(1 for r in records if r["hermes"]["format_violations"])
    haiku_fmt = sum(1 for r in records if r["haiku"]["format_violations"])
    hermes_lat = [r["hermes"]["latency_ms"] for r in records if r["hermes"]["summary"]]
    haiku_lat = [r["haiku"]["latency_ms"] for r in records if r["haiku"]["summary"]]

    print("\n" + "=" * 64)
    print(f"  SUMMARY EVAL  —  {n} items   (judge: {judged and judged[0]['judge']['engine'] or 'n/a'})")
    print("=" * 64)
    print(f"  Hermes model under test : {settings.hermes_model}")
    print(f"  Judged head-to-head     : {len(judged)}")
    if judged:
        print(f"    Hermes wins : {wins['hermes']}  ({100*wins['hermes']/len(judged):.0f}%)")
        print(f"    Haiku  wins : {wins['haiku']}  ({100*wins['haiku']/len(judged):.0f}%)")
        print(f"    Ties        : {wins['tie']}")
    print(f"  Engine failures (no output): hermes={hermes_fail}  haiku={haiku_fail}")
    print(f"  Format violations          : hermes={hermes_fmt}  haiku={haiku_fmt}")
    print(f"  Latency p50/p95 ms  hermes : {_pct(hermes_lat,0.5)} / {_pct(hermes_lat,0.95)}")
    print(f"  Latency p50/p95 ms  haiku  : {_pct(haiku_lat,0.5)} / {_pct(haiku_lat,0.95)}")
    if judged:
        print(f"  Mean judge scores (1-5):")
        print(f"    hermes : {_mean_scores(judged, 'hermes')}")
        print(f"    haiku  : {_mean_scores(judged, 'haiku')}")
    print("=" * 64 + "\n")


async def _run(types: list[str], n: int, concurrency: int, out_path: Path) -> None:
    try:
        await _run_inner(types, n, concurrency, out_path)
    finally:
        await close_es_client()


async def _run_inner(types: list[str], n: int, concurrency: int, out_path: Path) -> None:
    if not settings.hermes_enabled:
        logger.warning("HERMES_ENABLED is false — the Hermes side will return None for every item. "
                       "Set HERMES_ENABLED=true to run a real comparison.")
    await ensure_indices()
    items = await _sample_items(types, n)
    logger.info("Sampled %d items with usable source (requested %d) for types %s", len(items), n, types)
    if not items:
        logger.warning("No items to evaluate.")
        return

    sem = asyncio.Semaphore(concurrency)

    async def _one(item: dict) -> dict:
        async with sem:
            rec = await compare_engines(item)
        w = rec["judge"]["winner"] if rec.get("judge") else "no-judge"
        logger.info("Evaluated %-50s winner=%s", (item.get("title", "")[:50]), w)
        return rec

    records = await asyncio.gather(*[_one(it) for it in items])

    # Persist results to JSONL first — these are the costly API outputs, so never let an
    # ES hiccup throw them away.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    logger.info("Wrote %d records to %s", len(records), out_path)

    # Best-effort index to ES (generous timeout for serverless cold writes).
    es = get_es_client()
    indexed = 0
    for rec in records:
        try:
            await es.options(request_timeout=60).index(index=SUMMARY_EVAL_INDEX, document=rec)
            indexed += 1
        except Exception as e:
            logger.warning("ES index failed for '%s': %s", rec.get("title", "")[:40], e)
    logger.info("Indexed %d/%d records to %s", indexed, len(records), SUMMARY_EVAL_INDEX)

    _print_report(records)


def main() -> None:
    ap = argparse.ArgumentParser(description="Eval Haiku vs Hermes summarization")
    ap.add_argument("--n", type=int, default=20, help="number of items to evaluate")
    ap.add_argument("--types", default="video,podcast_episode,article",
                    help="comma-separated content types to sample")
    ap.add_argument("--concurrency", type=int, default=3, help="parallel evals (be kind to Hermes)")
    ap.add_argument("--out", default=None, help="JSONL output path (default: evals/<timestamp>.jsonl)")
    args = ap.parse_args()

    types = [t.strip() for t in args.types.split(",") if t.strip()]
    out = Path(args.out) if args.out else Path("evals") / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.jsonl"

    asyncio.run(_run(types, args.n, args.concurrency, out))


if __name__ == "__main__":
    main()
