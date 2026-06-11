"""Application configuration via pydantic-settings."""

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

# Load .env before BaseSettings reads env vars.
_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH)


class Settings(BaseSettings):
    model_config = {
        "env_prefix": "NANOCLAW_",
        "extra": "ignore",
    }

    # LLM configuration — all settings load from env or .env file
    llm_provider: str = "openai"
    llm_model: str = "deepseek-v4-pro"
    llm_base_url: str = "https://api.deepseek.com"
    openai_api_key: str = ""

    # Server configuration
    host: str = "127.0.0.1"
    port: int = 8420

    # Persistence
    db_path: str = "nanoclaw.db"

    def __init__(self, **kwargs):
        # Load from env / .env before prefix resolution
        for field in ("openai_api_key", "llm_model", "llm_base_url"):
            env_val = os.environ.get(field.upper(), "")
            if env_val and field not in kwargs:
                kwargs[field] = env_val
        super().__init__(**kwargs)


settings = Settings()
