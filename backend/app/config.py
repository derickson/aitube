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


settings = Settings()
