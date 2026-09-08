from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent

# Gemini free-tier ceilings, measured rather than quoted, because the published figures and
# the enforced ones disagreed: gemini-2.5-flash rejected above 5 requests a minute and capped
# the day at 20, while gemini-3.1-flash-lite sustained far more. These are the values a
# flash-lite extraction model tolerates, and they only ever produce a warning.
FREE_TIER_RPM = 15
FREE_TIER_CONCURRENCY = 3


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
    gemini_extraction_model: str = "gemini-3.1-flash-lite"
    gemini_adjudication_model: str = "gemini-3.1-flash-lite"
    gemini_vision_model: str = "gemini-3.1-flash-lite"
    # Output tokens the 2.5 models may spend reasoning before answering. Zero for reading
    # tasks; adjudication is genuinely a reasoning task and gets an allowance.
    gemini_thinking_budget: int = Field(default=1024, ge=0, le=24576)

    openrouter_api_key: str = ""
    openrouter_extraction_model: str = "google/gemini-2.5-flash"
    openrouter_adjudication_model: str = "google/gemini-2.5-flash"
    openrouter_vision_model: str = "google/gemini-2.5-flash"

    ollama_host: str = "http://localhost:11434"
    ollama_extraction_model: str = "qwen2.5:14b-instruct"
    ollama_adjudication_model: str = "qwen2.5:14b-instruct"

    llm_concurrency: int = Field(default=3, ge=1, le=64)
    llm_rate_limit_rpm: int = Field(default=15, ge=0)
    # Adjudication has its own budget: it may run on a different model from extraction,
    # and quotas are enforced per model.
    llm_adjudication_rate_limit_rpm: int = Field(default=12, ge=0)
    llm_cache_enabled: bool = True
    llm_max_calls_per_job: int = Field(default=0, ge=0)
    llm_timeout_seconds: float = Field(default=180.0, gt=0)
    llm_max_attempts: int = Field(default=4, ge=1, le=10)

    parse_workers: int = Field(default=0, ge=0, le=32)

    enable_vision: bool = True
    vision_render_dpi: int = Field(default=140, ge=72, le=400)

    # Share of a new document's substantive pages that must already be in the layer before
    # it is flagged as repeating another document. High on purpose: an annual report and
    # its successor share a great deal of boilerplate without one being a copy of the other,
    # and a false flag on a genuinely new filing is more costly than a missed duplicate.
    near_duplicate_ratio: float = Field(default=0.9, ge=0.0, le=1.0)

    # Adjudicate each escalated pair a second time with the two facts swapped, and keep the
    # answer only if it survives the swap. Doubles the cost of the smallest stage in the
    # pipeline to find out whether a verdict was about the evidence or about the ordering.
    adjudication_cross_check: bool = True

    # Render the relation table as a node graph as well. Off by default; see
    # graph_view_warning() for why.
    enable_graph_view: bool = False

    # Serve the layer for reading and refuse everything that changes it. Intended for a
    # deployment that shows the committed snapshot to reviewers: with this on, no API key is
    # needed and none should be present, because nothing can start a model call.
    read_only: bool = False

    # Recover text from pages that carry an image and no text layer. Off by default because
    # it needs Tesseract installed and costs roughly a second a page, and because an OCR
    # quote is a transcription rather than something read from the file — worth having, worth
    # knowing about. Scanned pages are *detected and reported* either way.
    enable_ocr: bool = False
    ocr_language: str = "eng"
    ocr_dpi: int = Field(default=300, ge=72, le=600)

    embedding_model: str = "BAAI/bge-small-en-v1.5"
    # ONNX threads for the embedding model. 0 chooses from the machine.
    embedding_threads: int = Field(default=0, ge=0, le=64)

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

    def throttle_warnings(self) -> list[str]:
        """Configuration that will probably be rate limited, and why.

        The defaults are safe, but the point of exposing these knobs is that someone with a
        paid key should be able to raise them. Blocking that would be wrong, and letting it
        fail silently would be worse — a run that spends twenty minutes absorbing 429s looks
        identical to a slow model. So over-limit settings are allowed and announced.

        The thresholds below are the documented Gemini free-tier ceilings at the time of
        writing. They move, so this warns rather than enforces.
        """
        if self.llm_provider.strip().lower() != "gemini":
            return []

        warnings: list[str] = []
        if self.llm_rate_limit_rpm == 0:
            warnings.append(
                "LLM_RATE_LIMIT_RPM is 0, which disables throttling entirely. On a free "
                f"Gemini key this will be rate limited almost immediately; "
                f"{FREE_TIER_RPM} is the safe value."
            )
        elif self.llm_rate_limit_rpm > FREE_TIER_RPM:
            warnings.append(
                f"LLM_RATE_LIMIT_RPM is {self.llm_rate_limit_rpm}, above the free Gemini "
                f"tier of roughly {FREE_TIER_RPM} requests per minute. Expect 429s. They "
                "are retried with backoff, so the run will finish, but slowly."
            )
        if self.llm_concurrency > FREE_TIER_CONCURRENCY:
            warnings.append(
                f"LLM_CONCURRENCY is {self.llm_concurrency}. Above {FREE_TIER_CONCURRENCY} "
                "in-flight requests a free key tends to reject rather than queue."
            )
        return warnings

    def graph_view_warning(self) -> str | None:
        """Why the graph view is off unless someone asks for it.

        The brief this project answers says plainly that a graph database or a visualisation
        is not the solution, and it is right: the work is in how facts are grounded,
        normalised and compared, and a picture of the result can be mistaken for that work
        having been done. The view is also, on this corpus, worse at its job than the table
        it sits beside — a few hundred nodes laid out by force is a shape, and the question a
        reviewer actually has is which two figures disagree and why.

        It is built anyway because there is one thing it shows that a sorted table cannot:
        which measures several publishers all describe, and whether the edges inside such a
        cluster agree. So it ships switched off, and turning it on says out loud what it is.
        """
        if not self.enable_graph_view:
            return None
        return (
            "ENABLE_GRAPH_VIEW is on. The graph is a rendering of the same relation rows the "
            "table shows, not a separate store and not the reasoning — read it for clusters, "
            "and use the table for verdicts and evidence."
        )

    @property
    def resolved_parse_workers(self) -> int:
        """Processes to spread page parsing across. Zero means choose from the machine."""
        if self.parse_workers:
            return self.parse_workers
        return max(1, min(8, (os.cpu_count() or 2) - 1))

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
