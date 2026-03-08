from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "agentic-coder"
    environment: str = Field(default="development", alias="APP_ENV")
    host: str = "0.0.0.0"
    port: int = 8080
    github_app_id: str = ""
    github_webhook_secret: str = "development-secret"
    github_private_key: str = ""
    github_api_base_url: str = "https://api.github.com"
    github_startup_self_check: bool = True
    github_startup_self_check_fail_fast: bool = False
    policy_path: Path = Path("agentic.yaml")
    database_url: str = "postgresql+psycopg://agentic_coder:agentic_coder@postgres:5432/agentic_coder"
    redis_url: str = "redis://redis:6379/0"
    queue_name: str = "agentic:tasks"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
