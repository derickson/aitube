from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # Elasticsearch Serverless
    elasticsearch_url: str = "http://localhost:9200"
    elasticsearch_api_key: str = ""

    # Content index + semantic search (flip to v2 + true after EIS reindex cutover)
    content_items_index: str = "aitube-content-items"
    enable_semantic_search: bool = False
    semantic_inference_id: str = ".jina-embeddings-v5-omni-nano"
    content_items_index_v2: str = "aitube-content-items-v2"

    # Anthropic
    anthropic_api_key: str = ""

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Content-DLP
    content_dlp_url: str = "http://localhost:7055"

    # Trusted-automation auth (aitube-sync, Hermes/Rex transcript judge, etc.)
    # When empty, write endpoints behind this auth are disabled (return 503).
    automation_token: str = ""

    # Elastic APM (Observability)
    elastic_apm_server_url: str = ""
    elastic_apm_secret_token: str = ""
    elastic_apm_api_key: str = ""
    elastic_apm_environment: str = "development"

    # Polling
    youtube_max_age_days: int = 5
    podcast_max_age_days: int = 5
    rss_max_age_days: int = 90

    # Topic-flow clustering (Jina task=clustering embeddings)
    jina_api_key: str = ""
    jina_embeddings_url: str = "https://api.jina.ai/v1/embeddings"
    # v5 with task="clustering" matches the blog post; v3 would use task="separation".
    jina_clustering_model: str = "jina-embeddings-v5-omni-nano"
    jina_clustering_task: str = "clustering"
    jina_clustering_dims: int = 768
    cluster_lookback_days: int = 30
    cluster_similarity_threshold: float = 0.62
    cluster_seed_separation: float = 0.55
    cluster_min_size: int = 3
    cluster_density_sample_pct: float = 0.05
    cluster_max_seeds: int = 40
    umap_neighbors: int = 15
    umap_min_dist: float = 0.1


settings = Settings()
