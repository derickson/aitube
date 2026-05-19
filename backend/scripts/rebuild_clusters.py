"""Crontab entry point for rebuilding topic-flow clusters.

Recommended cron entry (run nightly at 4am local):
    0 4 * * *  cd /path/to/aitube && uv run python -m backend.scripts.rebuild_clusters
"""

import asyncio
import logging

import elasticapm

from backend.app.config import settings
from backend.app.services.clustering import rebuild_clusters
from backend.app.services.elasticsearch import close_es_client, ensure_indices

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("elastic_transport.transport").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def _run():
    apm_client = None
    if settings.elastic_apm_server_url:
        apm_kwargs = {
            "service_name": "aitube-clusterer",
            "server_url": settings.elastic_apm_server_url,
            "environment": settings.elastic_apm_environment,
        }
        if settings.elastic_apm_api_key:
            apm_kwargs["api_key"] = settings.elastic_apm_api_key
        elif settings.elastic_apm_secret_token:
            apm_kwargs["secret_token"] = settings.elastic_apm_secret_token
        apm_client = elasticapm.Client(**apm_kwargs)
        elasticapm.instrument()

    try:
        if apm_client:
            apm_client.begin_transaction("script")
        await ensure_indices()
        summary = await rebuild_clusters()
        logger.info("rebuild_clusters summary: %s", summary)
        if apm_client:
            apm_client.end_transaction("rebuild_clusters", "success")
    except Exception:
        if apm_client:
            apm_client.capture_exception()
            apm_client.end_transaction("rebuild_clusters", "failure")
        raise
    finally:
        await close_es_client()
        if apm_client:
            apm_client.close()


def main():
    asyncio.run(_run())


if __name__ == "__main__":
    main()
