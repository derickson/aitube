from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    # Elasticsearch Serverless
    elasticsearch_url: str = "http://localhost:9200"
    elasticsearch_api_key: str = ""

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
    elastic_apm_environment: str = "development"

    # Polling
    youtube_max_age_days: int = 5
    podcast_max_age_days: int = 5
    rss_max_age_days: int = 90


settings = Settings()
