from elasticsearch import AsyncElasticsearch

from backend.app.config import settings

SUBSCRIPTIONS_INDEX = "aitube-subscriptions"
CONTENT_ITEMS_INDEX = settings.content_items_index
CONTENT_ITEMS_INDEX_V1 = "aitube-content-items"
CONTENT_ITEMS_INDEX_V2 = settings.content_items_index_v2
PLAYBACK_STATE_INDEX = "aitube-playback-state"
CLUSTER_RUNS_INDEX = "aitube-cluster-runs"


_CLUSTERING_FIELDS = {
    "clustering_vector": {
        "type": "dense_vector",
        "dims": settings.jina_clustering_dims,
        "index": True,
        "similarity": "cosine",
    },
    "clustering_vector_model": {"type": "keyword"},
    "cluster_id": {"type": "keyword"},
    "cluster_run_id": {"type": "keyword"},
    "umap_x": {"type": "float"},
    "umap_y": {"type": "float"},
}

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
    CONTENT_ITEMS_INDEX_V1: {
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
                "viewed": {"type": "boolean"},
                "user_interest": {"type": "keyword"},
                "content_markdown": {"type": "text"},
                "content_dlp_cache_id": {"type": "keyword"},
                "metadata": {"type": "object", "enabled": False},
                **_CLUSTERING_FIELDS,
            }
        }
    },
    CONTENT_ITEMS_INDEX_V2: {
        "mappings": {
            "properties": {
                "subscription_id": {"type": "keyword"},
                "external_id": {"type": "keyword"},
                "type": {"type": "keyword"},
                "title": {
                    "type": "text",
                    "fields": {"keyword": {"type": "keyword"}},
                    "copy_to": "semantic_headline",
                },
                "url": {"type": "keyword"},
                "published_at": {"type": "date"},
                "discovered_at": {"type": "date"},
                "duration_seconds": {"type": "float"},
                "thumbnail_url": {"type": "keyword", "index": False},
                "summary": {"type": "text", "copy_to": "semantic_headline"},
                "interest_score": {"type": "float"},
                "interest_reasoning": {"type": "text"},
                "transcript": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "text", "copy_to": "semantic_body"},
                        "chunks": {"type": "object", "enabled": False},
                    },
                },
                "consumed": {"type": "boolean"},
                "viewed": {"type": "boolean"},
                "user_interest": {"type": "keyword"},
                "content_markdown": {"type": "text", "copy_to": "semantic_body"},
                "content_dlp_cache_id": {"type": "keyword"},
                "metadata": {"type": "object", "enabled": False},
                "semantic_headline": {
                    "type": "semantic_text",
                    "inference_id": settings.semantic_inference_id,
                },
                "semantic_body": {
                    "type": "semantic_text",
                    "inference_id": settings.semantic_inference_id,
                },
                **_CLUSTERING_FIELDS,
            }
        }
    },
    CLUSTER_RUNS_INDEX: {
        "mappings": {
            "properties": {
                "run_id": {"type": "keyword"},
                "created_at": {"type": "date"},
                "lookback_days": {"type": "integer"},
                "doc_count": {"type": "integer"},
                "noise_count": {"type": "integer"},
                "embedding_model": {"type": "keyword"},
                "params": {"type": "object", "enabled": False},
                "clusters": {"type": "object", "enabled": False},
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
