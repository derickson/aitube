"""Crontab entry point for polling all active feeds."""

import asyncio
import logging

from backend.app.services.feed_poller import poll_all_active
from backend.app.services.elasticsearch import close_es_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def _run():
    try:
        results = await poll_all_active()
        total = sum(len(ids) for ids in results.values())
        logger.info("Poll complete: %d new items across %d subscriptions", total, len(results))
    finally:
        await close_es_client()


def main():
    asyncio.run(_run())


if __name__ == "__main__":
    main()
