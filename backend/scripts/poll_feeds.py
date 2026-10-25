"""Crontab entry point for polling all active feeds."""

import asyncio
import logging

from backend.app.services import apm
from backend.app.services.feed_poller import poll_all_active
from backend.app.services.elasticsearch import close_es_client

apm.configure_logging()
# Reduce noise from HTTP client and Elasticsearch transport during polling
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("elastic_transport.transport").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def _run():
    # Transactions are opened per subscription and per post-poll phase inside
    # poll_all_active(); one transaction for the whole run could exceed the agent's
    # 500-span cap and lumps every feed into a single number.
    apm_client = apm.init_script_client("aitube-poller")
    try:
        results = await poll_all_active()
        total = sum(len(ids) for ids in results.values())
        logger.info("Poll complete: %d new items across %d subscriptions", total, len(results))
    finally:
        await close_es_client()
        if apm_client:
            apm_client.close()


def main():
    asyncio.run(_run())


if __name__ == "__main__":
    main()
