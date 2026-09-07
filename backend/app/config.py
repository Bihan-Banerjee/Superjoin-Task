from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    llm_provider: str = "gemini"
    llm_fallback_provider: str = ""

    gemini_api_key: str = ""
    gemini_extraction_model: str = "gemini-2.5-flash"
    gemini_adjudication_model: str = "gemini-2.5-flash"
    gemini_vision_model: str = "gemini-2.5-flash"

    openrouter_api_key: str = ""
    openrouter_extraction_model: str = "google/gemini-2.5-flash"
    openrouter_adjudication_model: str = "google/gemini-2.5-flash"
    openrouter_vision_model: str = "google/gemini-2.5-flash"

    ollama_host: str = "http://localhost:11434"
    ollama_extraction_model: str = "qwen2.5:14b-instruct"
    ollama_adjudication_model: str = "qwen2.5:14b-instruct"

    llm_concurrency: int = Field(default=6, ge=1, le=64)
    llm_rate_limit_rpm: int = Field(default=60, ge=0)
    llm_cache_enabled: bool = True
    llm_max_calls_per_job: int = Field(default=0, ge=0)
    llm_timeout_seconds: float = Field(default=180.0, gt=0)
    llm_max_attempts: int = Field(default=4, ge=1, le=10)

    enable_vision: bool = True
    vision_render_dpi: int = Field(default=140, ge=72, le=400)

    embedding_model: str = "BAAI/bge-small-en-v1.5"

    data_dir: Path = Path("./data")
    database_url: str = ""
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173"
    log_level: str = "INFO"

    max_upload_mb: int = Field(default=200, ge=1)

    @field_validator("data_dir", mode="before")
    @classmethod
    def _resolve_data_dir(cls, value: object) -> Path:
        path = Path(str(value))
        if not path.is_absolute():
            path = (BACKEND_ROOT / path).resolve()
        return path

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "llm-cache"

    @property
    def replay_dir(self) -> Path:
        return BACKEND_ROOT / "seed" / "replay"

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+pysqlite:///{(self.data_dir / 'knowledge.sqlite').as_posix()}"

    def ensure_directories(self) -> None:
        for path in (self.data_dir, self.upload_dir, self.cache_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()


def is_truthy(value: str | None) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


REPLAY_ONLY = is_truthy(os.getenv("REPLAY_ONLY"))
