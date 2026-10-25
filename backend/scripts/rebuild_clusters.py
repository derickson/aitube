"""Crontab entry point for rebuilding topic-flow clusters.

Recommended cron entry (run nightly at 4am local):
    0 4 * * *  cd /path/to/aitube && uv run python -m backend.scripts.rebuild_clusters
"""

import asyncio
import logging

from backend.app.services import apm
from backend.app.services.clustering import rebuild_clusters
from backend.app.services.elasticsearch import close_es_client, ensure_indices

apm.configure_logging()
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("elastic_transport.transport").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def _run():
    apm_client = apm.init_script_client("aitube-clusterer")
    try:
        async with apm.transaction("rebuild_clusters", "script"):
            await ensure_indices()
            summary = await rebuild_clusters()
            logger.info("rebuild_clusters summary: %s", summary)
    finally:
        await close_es_client()
        if apm_client:
            apm_client.close()


def main():
    asyncio.run(_run())


if __name__ == "__main__":
    main()
