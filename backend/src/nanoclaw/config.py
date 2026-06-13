"""Application configuration via pydantic-settings."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import computed_field
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

    # LLM configuration
    llm_provider: str = "openai"
    llm_model: str = "deepseek-v4-pro"
    llm_base_url: str = "https://api.deepseek.com"
    openai_api_key: str = ""

    # Server configuration
    host: str = "127.0.0.1"
    port: int = 8420

    # Persistence
    db_path: str = "nanoclaw.db"

    # Phase 3: Memory & Evaluation root directory
    # Set via NANOCLAW_HOME env var. Defaults to .nanoclaw in CWD.
    home: str = ".nanoclaw"

    @computed_field  # type: ignore[misc]
    @property
    def chroma_persist_dir(self) -> str:
        """ChromaDB persistent storage directory under home."""
        return str(Path(self.home) / "memory" / "chroma")

    @computed_field  # type: ignore[misc]
    @property
    def eval_dir(self) -> str:
        """Evaluation log directory under home."""
        return str(Path(self.home) / "eval")

    def __init__(self, **kwargs):
        # Load from env / .env before prefix resolution
        for field in ("openai_api_key", "llm_model", "llm_base_url"):
            env_val = os.environ.get(field.upper(), "")
            if env_val and field not in kwargs:
                kwargs[field] = env_val
        super().__init__(**kwargs)


settings = Settings()
