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

    # Elastic APM (Observability)
    elastic_apm_server_url: str = ""
    elastic_apm_secret_token: str = ""
    elastic_apm_api_key: str = ""
    elastic_apm_environment: str = "development"

    # Polling
    youtube_max_age_days: int = 5
    podcast_max_age_days: int = 5
    rss_max_age_days: int = 90


settings = Settings()
