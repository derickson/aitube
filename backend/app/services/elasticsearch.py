from elasticsearch import AsyncElasticsearch

from backend.app.config import settings

SUBSCRIPTIONS_INDEX = "aitube-subscriptions"
CONTENT_ITEMS_INDEX = "aitube-content-items"
PLAYBACK_STATE_INDEX = "aitube-playback-state"

_client: AsyncElasticsearch | None = None


def get_es_client() -> AsyncElasticsearch:
    global _client
    if _client is None:
        _client = AsyncElasticsearch(
            settings.elasticsearch_url,
            api_key=settings.elasticsearch_api_key,
        )
    return _client


async def close_es_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


INDEX_MAPPINGS: dict[str, dict] = {
    SUBSCRIPTIONS_INDEX: {
        "mappings": {
            "properties": {
                "type": {"type": "keyword"},
                "url": {"type": "keyword"},
                "name": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                "description": {"type": "text"},
                "interest_notes": {"type": "text"},
                "status": {"type": "keyword"},
                "added_at": {"type": "date"},
                "last_polled_at": {"type": "date"},
            }
        }
    },
    CONTENT_ITEMS_INDEX: {
        "mappings": {
            "properties": {
                "subscription_id": {"type": "keyword"},
                "external_id": {"type": "keyword"},
                "type": {"type": "keyword"},
                "title": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                "url": {"type": "keyword"},
                "published_at": {"type": "date"},
                "discovered_at": {"type": "date"},
                "duration_seconds": {"type": "float"},
                "thumbnail_url": {"type": "keyword", "index": False},
                "summary": {"type": "text"},
                "interest_score": {"type": "float"},
                "interest_reasoning": {"type": "text"},
                "transcript": {"type": "object", "enabled": False},
                "consumed": {"type": "boolean"},
                "user_interest": {"type": "keyword"},
                "content_markdown": {"type": "text"},
                "content_dlp_cache_id": {"type": "keyword"},
                "metadata": {"type": "object", "enabled": False},
            }
        }
    },
    PLAYBACK_STATE_INDEX: {
        "mappings": {
            "properties": {
                "content_item_id": {"type": "keyword"},
                "position_seconds": {"type": "float"},
                "consumed": {"type": "boolean"},
                "last_updated_at": {"type": "date"},
            }
        }
    },
}


async def ensure_indices() -> None:
    es = get_es_client()
    for index_name, body in INDEX_MAPPINGS.items():
        if not await es.indices.exists(index=index_name):
            await es.indices.create(index=index_name, body=body)
        else:
            # Update mappings for any new fields on existing indices
            try:
                await es.indices.put_mapping(
                    index=index_name,
                    body=body["mappings"],
                )
            except Exception:
                pass  # Ignore conflicts with existing field types
