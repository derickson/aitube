"""Crontab entry point for polling all active feeds."""

import asyncio
import logging

import elasticapm

from backend.app.config import settings
from backend.app.services.feed_poller import poll_all_active
from backend.app.services.elasticsearch import close_es_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# Reduce noise from HTTP client and Elasticsearch transport during polling
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("elastic_transport.transport").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def _run():
    apm_client = None
    if settings.elastic_apm_server_url:
        apm_client = elasticapm.Client(
            service_name="aitube-poller",
            server_url=settings.elastic_apm_server_url,
            secret_token=settings.elastic_apm_secret_token,
            environment=settings.elastic_apm_environment,
        )
        elasticapm.instrument()

    try:
        if apm_client:
            apm_client.begin_transaction("script")
        results = await poll_all_active()
        total = sum(len(ids) for ids in results.values())
        logger.info("Poll complete: %d new items across %d subscriptions", total, len(results))
        if apm_client:
            apm_client.end_transaction("poll_all_active", "success")
    except Exception:
        if apm_client:
            apm_client.capture_exception()
            apm_client.end_transaction("poll_all_active", "failure")
        raise
    finally:
        await close_es_client()
        if apm_client:
            apm_client.close()


def main():
    asyncio.run(_run())


if __name__ == "__main__":
    main()
