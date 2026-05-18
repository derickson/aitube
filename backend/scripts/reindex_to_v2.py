"""Reindex aitube-content-items -> aitube-content-items-v2 with EIS semantic_text.

Usage:
    uv run python -m backend.scripts.reindex_to_v2 verify
    uv run python -m backend.scripts.reindex_to_v2 start [--rps 200] [--slices auto]
    uv run python -m backend.scripts.reindex_to_v2 status <task_id>
    uv run python -m backend.scripts.reindex_to_v2 delta --since <iso8601> [--rps 200]
    uv run python -m backend.scripts.reindex_to_v2 counts
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone

from backend.app.config import settings
from backend.app.services.elasticsearch import (
    CONTENT_ITEMS_INDEX_V1,
    CONTENT_ITEMS_INDEX_V2,
    close_es_client,
    ensure_indices,
    get_es_client,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("reindex_v2")


async def cmd_verify() -> int:
    es = get_es_client()
    inference_id = settings.semantic_inference_id
    log.info("Checking inference endpoint: %s", inference_id)
    try:
        resp = await es.perform_request("GET", f"/_inference/{inference_id}")
        log.info("OK: %s", resp.body if hasattr(resp, "body") else resp)
        return 0
    except Exception as e:
        log.error("Inference endpoint %s not reachable: %s", inference_id, e)
        return 1


async def cmd_start(rps: int, slices: str) -> int:
    es = get_es_client()
    log.info("Ensuring indices exist (v1 + v2)...")
    await ensure_indices()

    start_ts = datetime.now(timezone.utc).isoformat()
    log.info("Reindex start timestamp (save for delta step): %s", start_ts)

    body = {
        "source": {"index": CONTENT_ITEMS_INDEX_V1, "size": 100},
        "dest": {"index": CONTENT_ITEMS_INDEX_V2, "op_type": "index"},
    }
    slices_param: str | int = slices if slices == "auto" else int(slices)
    log.info(
        "Submitting reindex %s -> %s (rps=%s, slices=%s, wait_for_completion=false)",
        CONTENT_ITEMS_INDEX_V1, CONTENT_ITEMS_INDEX_V2, rps, slices_param,
    )
    resp = await es.reindex(
        body=body,
        wait_for_completion=False,
        requests_per_second=float(rps),
        slices=slices_param,
        refresh=False,
    )
    task_id = resp.get("task")
    log.info("Reindex task submitted. task_id=%s", task_id)
    log.info("Poll with: uv run python -m backend.scripts.reindex_to_v2 status %s", task_id)
    log.info("After completion run: uv run python -m backend.scripts.reindex_to_v2 delta --since %s", start_ts)
    return 0


async def cmd_status(task_id: str) -> int:
    es = get_es_client()
    resp = await es.tasks.get(task_id=task_id)
    completed = resp.get("completed", False)
    task = resp.get("task", {})
    status = task.get("status", {})
    total = status.get("total", 0)
    created = status.get("created", 0)
    updated = status.get("updated", 0)
    deleted = status.get("deleted", 0)
    batches = status.get("batches", 0)
    failures = resp.get("response", {}).get("failures", []) if completed else []
    log.info(
        "completed=%s total=%s created=%s updated=%s deleted=%s batches=%s",
        completed, total, created, updated, deleted, batches,
    )
    if completed:
        took = resp.get("response", {}).get("took")
        timed_out = resp.get("response", {}).get("timed_out")
        log.info("took_ms=%s timed_out=%s failures=%d", took, timed_out, len(failures))
        if failures:
            for f in failures[:5]:
                log.error("FAILURE: %s", f)
            if len(failures) > 5:
                log.error("... and %d more failures", len(failures) - 5)
            return 2
    return 0 if completed else 3


async def cmd_delta(since: str, rps: int) -> int:
    es = get_es_client()
    log.info("Delta reindex: docs with discovered_at >= %s", since)
    body = {
        "source": {
            "index": CONTENT_ITEMS_INDEX_V1,
            "size": 100,
            "query": {"range": {"discovered_at": {"gte": since}}},
        },
        "dest": {"index": CONTENT_ITEMS_INDEX_V2, "op_type": "index"},
    }
    resp = await es.reindex(
        body=body,
        wait_for_completion=False,
        requests_per_second=float(rps),
        slices="auto",
        refresh=False,
    )
    task_id = resp.get("task")
    log.info("Delta reindex submitted. task_id=%s", task_id)
    log.info("Poll with: uv run python -m backend.scripts.reindex_to_v2 status %s", task_id)
    return 0


async def cmd_counts() -> int:
    es = get_es_client()
    v1 = await es.count(index=CONTENT_ITEMS_INDEX_V1)
    try:
        v2 = await es.count(index=CONTENT_ITEMS_INDEX_V2)
        v2_count = v2["count"]
    except Exception as e:
        v2_count = f"(missing: {e})"
    log.info("%s: %s", CONTENT_ITEMS_INDEX_V1, v1["count"])
    log.info("%s: %s", CONTENT_ITEMS_INDEX_V2, v2_count)
    return 0


async def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("verify", help="Check that the EIS inference endpoint is reachable")
    sub.add_parser("counts", help="Show doc counts for v1 and v2")

    p_start = sub.add_parser("start", help="Submit the main reindex task (async)")
    p_start.add_argument("--rps", type=int, default=200, help="requests_per_second throttle")
    p_start.add_argument("--slices", default="auto", help="slices (int or 'auto')")

    p_status = sub.add_parser("status", help="Poll a reindex task by id")
    p_status.add_argument("task_id")

    p_delta = sub.add_parser("delta", help="Reindex docs added since main reindex started")
    p_delta.add_argument("--since", required=True, help="ISO 8601 timestamp")
    p_delta.add_argument("--rps", type=int, default=200)

    args = parser.parse_args(argv)

    try:
        if args.cmd == "verify":
            return await cmd_verify()
        if args.cmd == "start":
            return await cmd_start(args.rps, args.slices)
        if args.cmd == "status":
            return await cmd_status(args.task_id)
        if args.cmd == "delta":
            return await cmd_delta(args.since, args.rps)
        if args.cmd == "counts":
            return await cmd_counts()
        return 1
    finally:
        await close_es_client()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
