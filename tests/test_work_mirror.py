"""Work mirror against two real Elasticsearch clusters.

Skipped unless both test clusters are given (they are wiped, so never point these
at real clusters):

    docker run -d --rm --name aitube-test-es1 -p 127.0.0.1:19201:9200 \\
      -e discovery.type=single-node -e xpack.security.enabled=false \\
      docker.elastic.co/elasticsearch/elasticsearch:9.2.4
    (same with es2 / 19202)
    AITUBE_TEST_PROD_ES=http://127.0.0.1:19201 AITUBE_TEST_WORK_ES=http://127.0.0.1:19202 \\
      uv run pytest tests/test_work_mirror.py
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from elasticsearch import AsyncElasticsearch

from backend.app.config import settings
from backend.app.services import elasticsearch as es_service
from backend.app.services import work_mirror
from backend.app.services.elasticsearch import (
    CATEGORY_CONFIG_INDEX,
    CONTENT_ITEMS_INDEX,
    INDEX_MAPPINGS,
    SUBSCRIPTIONS_INDEX,
)

PROD_URL = os.environ.get("AITUBE_TEST_PROD_ES")
WORK_URL = os.environ.get("AITUBE_TEST_WORK_ES")

pytestmark = [
    pytest.mark.skipif(not (PROD_URL and WORK_URL), reason="needs AITUBE_TEST_PROD_ES and AITUBE_TEST_WORK_ES"),
    pytest.mark.asyncio,
]

NOW = datetime.now(timezone.utc)
RECENT = (NOW - timedelta(days=1)).isoformat()
OLD = (NOW - timedelta(days=60)).isoformat()


def _sub(name: str, mirror: bool) -> dict:
    return {
        "type": "youtube_channel", "url": f"https://yt/{name}", "name": name,
        "interest_notes": "private notes", "status": "active",
        "added_at": OLD, "last_polled_at": RECENT, "mirror_to_work": mirror,
    }


def _item(sub_id: str, title: str, discovered: str, **extra) -> dict:
    return {
        "subscription_id": sub_id, "type": "video", "title": title, "url": f"https://v/{title}",
        "discovered_at": discovered, "published_at": discovered, "summary": f"summary of {title}",
        "category": "AI and Software", "consumed": True, "viewed": True, "user_interest": "up",
        "summary_error": None, **extra,
    }


@pytest_asyncio.fixture
async def clusters(monkeypatch):
    prod = AsyncElasticsearch(PROD_URL)
    work = AsyncElasticsearch(WORK_URL)
    for client in (prod, work):
        for name in INDEX_MAPPINGS:  # by name: ES 9 refuses wildcard deletes
            await client.options(ignore_status=404).indices.delete(index=name)
    for name in (SUBSCRIPTIONS_INDEX, CONTENT_ITEMS_INDEX, CATEGORY_CONFIG_INDEX):
        await prod.indices.create(index=name, body=INDEX_MAPPINGS[name])

    monkeypatch.setattr(settings, "work_elasticsearch_url", WORK_URL)
    monkeypatch.setattr(settings, "work_elasticsearch_api_key", "")
    monkeypatch.setattr(es_service, "_client", prod)  # get_es_client() -> prod
    await work_mirror.close_work_client()

    await prod.index(index=SUBSCRIPTIONS_INDEX, id="flagged", document=_sub("flagged", True))
    await prod.index(index=SUBSCRIPTIONS_INDEX, id="private", document=_sub("private", False))
    await prod.index(index=CATEGORY_CONFIG_INDEX, id="default", document={"categories": [{"slug": "x", "label": "X"}]})
    await prod.index(index=CONTENT_ITEMS_INDEX, id="f-recent", document=_item("flagged", "f-recent", RECENT))
    await prod.index(index=CONTENT_ITEMS_INDEX, id="f-old", document=_item("flagged", "f-old", OLD))
    await prod.index(index=CONTENT_ITEMS_INDEX, id="p-recent", document=_item("private", "p-recent", RECENT))
    await prod.index(index=CONTENT_ITEMS_INDEX, id="adhoc-1", document=_item("adhoc", "adhoc-1", RECENT))
    await prod.indices.refresh(index="aitube-*")

    yield prod, work

    await work_mirror.close_work_client()
    await prod.close()
    await work.close()


async def _work_ids(work: AsyncElasticsearch) -> set[str]:
    await work.indices.refresh(index=CONTENT_ITEMS_INDEX)
    resp = await work.search(index=CONTENT_ITEMS_INDEX, query={"match_all": {}}, size=100, source=False)
    return {h["_id"] for h in resp["hits"]["hits"]}


async def test_full_sync_mirrors_only_flagged_subscriptions(clusters):
    prod, work = clusters
    result = await work_mirror.sync_to_work(days=None)

    assert result.errors == []
    assert result.subscriptions == ["flagged"]
    assert await _work_ids(work) == {"f-recent", "f-old"}

    doc = (await work.get(index=CONTENT_ITEMS_INDEX, id="f-recent"))["_source"]
    assert doc["summary"] == "summary of f-recent"
    for personal in ("consumed", "viewed", "user_interest", "summary_error"):
        assert personal not in doc

    sub = (await work.get(index=SUBSCRIPTIONS_INDEX, id="flagged"))["_source"]
    assert sub["name"] == "flagged" and "interest_notes" not in sub
    assert not (await work.exists(index=SUBSCRIPTIONS_INDEX, id="private"))
    assert (await work.get(index=CATEGORY_CONFIG_INDEX, id="default"))["_source"]["categories"][0]["slug"] == "x"


async def test_windowed_sync_skips_old_items_but_picks_up_updates(clusters):
    prod, work = clusters
    assert (await work_mirror.sync_to_work(days=14)).upserted == 1
    assert await _work_ids(work) == {"f-recent"}

    await prod.update(index=CONTENT_ITEMS_INDEX, id="f-recent", doc={"summary": "better summary"}, refresh=True)
    await work_mirror.sync_to_work(days=14)
    assert (await work.get(index=CONTENT_ITEMS_INDEX, id="f-recent"))["_source"]["summary"] == "better summary"


async def test_sync_deletes_items_prod_removed_and_unflagged_subscriptions(clusters):
    prod, work = clusters
    await work_mirror.sync_to_work(days=None)

    # prod dedup removes one item; the other subscription loses its flag
    await prod.delete(index=CONTENT_ITEMS_INDEX, id="f-recent", refresh=True)
    await work_mirror.sync_to_work(days=14)
    assert await _work_ids(work) == {"f-old"}

    await prod.update(index=SUBSCRIPTIONS_INDEX, id="flagged", doc={"mirror_to_work": False}, refresh=True)
    result = await work_mirror.sync_to_work(days=14)
    assert result.subscriptions == []
    assert await _work_ids(work) == set()
    assert not (await work.exists(index=SUBSCRIPTIONS_INDEX, id="flagged"))


async def test_work_side_fields_survive_resync(clusters):
    _, work = clusters
    await work_mirror.sync_to_work(days=None)
    await work.update(index=CONTENT_ITEMS_INDEX, id="f-recent", doc={"viewed": True}, refresh=True)
    await work_mirror.sync_to_work(days=None)
    assert (await work.get(index=CONTENT_ITEMS_INDEX, id="f-recent"))["_source"]["viewed"] is True


async def test_unmirror_subscription_purges_it(clusters):
    _, work = clusters
    await work_mirror.sync_to_work(days=None)
    assert await work_mirror.unmirror_subscription("flagged") == 2
    assert await _work_ids(work) == set()
    assert not (await work.exists(index=SUBSCRIPTIONS_INDEX, id="flagged"))


async def test_dry_run_writes_nothing(clusters):
    _, work = clusters
    result = await work_mirror.sync_to_work(days=None, dry_run=True)
    assert result.upserted == 2
    assert not (await work.indices.exists(index=CONTENT_ITEMS_INDEX))


async def test_unconfigured_mirror_is_a_noop(clusters, monkeypatch):
    monkeypatch.setattr(settings, "work_elasticsearch_url", "")
    await work_mirror.close_work_client()
    result = await work_mirror.sync_to_work(days=None)
    assert result.subscriptions == [] and result.upserted == 0
