"""Application configuration via pydantic-settings."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "NANOCLAW_"}

    # LLM configuration
    llm_provider: str = "openai"  # "openai" or "anthropic"
    llm_model: str = "gpt-4o-mini"
    # API keys loaded from environment
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    # Server configuration
    host: str = "127.0.0.1"
    port: int = 8420

    # Persistence
    db_path: str = "nanoclaw.db"


settings = Settings()
