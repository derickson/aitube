"""Sync flagged subscriptions from the prod cluster into the work cluster.

The poller already runs a windowed sync every cycle; use this for the initial
backfill, after changing which fields are mirrored, or to repair drift.

    uv run python -m backend.scripts.sync_to_work --dry-run          # report only
    uv run python -m backend.scripts.sync_to_work                    # last WORK_MIRROR_DAYS
    uv run python -m backend.scripts.sync_to_work --full             # all time
    uv run python -m backend.scripts.sync_to_work --full --sub <id>  # one subscription

Needs WORK_ELASTICSEARCH_URL (and usually WORK_ELASTICSEARCH_API_KEY) in .env.
"""

import argparse
import asyncio
import logging
import sys

from backend.app.config import settings
from backend.app.services import work_mirror
from backend.app.services.elasticsearch import close_es_client

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("elastic_transport.transport").setLevel(logging.WARNING)


async def _run(args: argparse.Namespace) -> int:
    if not work_mirror.is_configured():
        print("WORK_ELASTICSEARCH_URL is not set; nothing to sync to.", file=sys.stderr)
        return 2
    days = None if args.full else args.days
    try:
        result = await work_mirror.sync_to_work(
            days=days, sub_ids=args.sub or None, dry_run=args.dry_run
        )
    finally:
        await close_es_client()
        await work_mirror.close_work_client()
    print(result.summary())
    for sid in result.subscriptions:
        print(f"  subscription {sid}")
    return 1 if result.errors else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--full", action="store_true", help="sync all time, not just the recent window")
    parser.add_argument("--days", type=int, default=settings.work_mirror_days, help="window in days (default %(default)s)")
    parser.add_argument("--sub", action="append", help="limit to this subscription id (repeatable)")
    parser.add_argument("--dry-run", action="store_true", help="count what would change, write nothing")
    sys.exit(asyncio.run(_run(parser.parse_args())))


if __name__ == "__main__":
    main()
