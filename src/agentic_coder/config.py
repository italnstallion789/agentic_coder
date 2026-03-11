from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "agentic-coder"
    environment: str = Field(default="development", alias="APP_ENV")
    host: str = "0.0.0.0"
    port: int = 8080
    github_app_id: str = ""
    github_webhook_secret: str = "development-secret"
    github_private_key: str = ""
    github_private_key_path: Path | None = None
    github_api_base_url: str = "https://api.github.com"
    github_models_api_key: str = ""
    github_models_base_url: str = "https://models.inference.ai.azure.com"
    github_models_chat_model: str = "gpt-4.1-mini"
    github_startup_self_check: bool = True
    github_startup_self_check_fail_fast: bool = False
    policy_path: Path = Path("agentic.yaml")
    database_url: str = "postgresql+psycopg://agentic_coder:agentic_coder@postgres:5432/agentic_coder"
    redis_url: str = "redis://redis:6379/0"
    queue_name: str = "agentic:tasks"
    ollama_base_url: str = "http://ollama:11434"
    ollama_chat_model: str = "llama3.1:8b"
    model_request_timeout_seconds: float = 40.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @model_validator(mode="after")
    def load_private_key_from_path(self) -> "Settings":
        if self.github_private_key:
            return self
        if self.github_private_key_path is None:
            return self

        path_value = str(self.github_private_key_path).strip()
        if path_value in {"", "."}:
            return self

        key_path = Path(path_value)
        if not key_path.is_absolute():
            key_path = Path.cwd() / key_path
        if not key_path.exists() or not key_path.is_file():
            return self
        self.github_private_key = key_path.read_text(encoding="utf-8")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
