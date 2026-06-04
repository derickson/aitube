from elasticsearch import AsyncElasticsearch

from backend.app.config import settings

SUBSCRIPTIONS_INDEX = "aitube-subscriptions"
CONTENT_ITEMS_INDEX = settings.content_items_index
CONTENT_ITEMS_INDEX_V1 = "aitube-content-items"
CONTENT_ITEMS_INDEX_V2 = settings.content_items_index_v2
PLAYBACK_STATE_INDEX = "aitube-playback-state"
CLUSTER_RUNS_INDEX = "aitube-cluster-runs"
QUARANTINE_EVENTS_INDEX = "aitube-quarantine-events"
SUMMARY_EVAL_INDEX = settings.summary_eval_index


# Ingest pipeline that runs the custom engagement classifier (loaded from the
# aitube-prediction-model sister project). Produces an `engagement` object with
# `score` = P(engaged) and `prediction` ("engaged"/"not_engaged").
ENGAGEMENT_PIPELINE = "aitube-engagement"

_ENGAGEMENT_FIELDS = {
    "engagement": {
        "properties": {
            "prediction": {"type": "keyword"},
            "score": {"type": "float"},
            "prediction_probability": {"type": "float"},
            "model_id": {"type": "keyword"},
            "probabilities": {
                "type": "nested",
                "properties": {
                    "class_name": {"type": "keyword"},
                    "class_probability": {"type": "float"},
                },
            },
        }
    },
}

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
                "quarantined_at": {"type": "date"},
                "quarantine_reason_code": {"type": "keyword"},
                "quarantine_reason": {"type": "text"},
                "quarantine_source": {"type": "keyword"},
                **_ENGAGEMENT_FIELDS,
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
                "quarantined_at": {"type": "date"},
                "quarantine_reason_code": {"type": "keyword"},
                "quarantine_reason": {"type": "text"},
                "quarantine_source": {"type": "keyword"},
                "semantic_headline": {
                    "type": "semantic_text",
                    "inference_id": settings.semantic_inference_id,
                },
                "semantic_body": {
                    "type": "semantic_text",
                    "inference_id": settings.semantic_inference_id,
                },
                **_ENGAGEMENT_FIELDS,
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
    QUARANTINE_EVENTS_INDEX: {
        "mappings": {
            "properties": {
                "content_item_id": {"type": "keyword"},
                "external_id": {"type": "keyword"},
                "source": {"type": "keyword"},
                "reason_code": {"type": "keyword"},
                "reason": {"type": "text"},
                "transcript_excerpt_sha256": {"type": "keyword"},
                "created_at": {"type": "date"},
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
    SUMMARY_EVAL_INDEX: {
        "mappings": {
            "properties": {
                "item_id": {"type": "keyword"},
                "external_id": {"type": "keyword"},
                "title": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                "type": {"type": "keyword"},
                "evaluated_at": {"type": "date"},
                "haiku": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "text"},
                        "latency_ms": {"type": "float"},
                        "format_violations": {"type": "keyword"},
                    },
                },
                "hermes": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "text"},
                        "model": {"type": "keyword"},
                        "latency_ms": {"type": "float"},
                        "format_violations": {"type": "keyword"},
                    },
                },
                "judge": {
                    "type": "object",
                    "properties": {
                        "engine": {"type": "keyword"},
                        "winner": {"type": "keyword"},
                        "scores": {"type": "object", "enabled": False},
                        "rationale": {"type": "text"},
                    },
                },
            }
        }
    },
}


async def _ensure_default_pipeline(es: AsyncElasticsearch, index_name: str) -> None:
    """Attach the engagement classifier as the index default_pipeline so every
    newly indexed item is scored on ingest. No-op if the pipeline isn't loaded
    (it's managed by the aitube-prediction-model project) to avoid breaking
    indexing with a missing-pipeline error."""
    try:
        await es.ingest.get_pipeline(id=ENGAGEMENT_PIPELINE)
    except Exception:
        return  # pipeline not deployed; leave indexing untouched
    try:
        await es.indices.put_settings(
            index=index_name,
            body={"index.default_pipeline": ENGAGEMENT_PIPELINE},
        )
    except Exception:
        pass


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

    # Wire the engagement classifier onto the content-item indices.
    for index_name in (CONTENT_ITEMS_INDEX_V1, CONTENT_ITEMS_INDEX_V2):
        if await es.indices.exists(index=index_name):
            await _ensure_default_pipeline(es, index_name)
