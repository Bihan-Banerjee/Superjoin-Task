"""Shared fixtures.

The integration tests run the real pipeline — real parsing, real grounding, real
normalisation, real rules — against a stub model. That combination is deliberate: the parts
worth testing are the ones this project actually wrote, and a network call in a test would
make the suite slow, expensive and dependent on a model's mood.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings, get_settings, reset_settings_cache
from app.db import engine as engine_module
from app.db.engine import init_database
from app.llm.base import LlmError, LlmRequest, LlmResponse, Provider


class StubProvider(Provider):
    """Serves canned responses keyed by a marker in the prompt.

    Requests are recorded so a test can assert on what the pipeline asked for — how pages
    were batched, whether an image was attached, which stage a call belonged to — which is
    most of what there is to check about prompt assembly.
    """

    name = "stub"

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.responses: dict[str, Any] = responses or {}
        self.requests: list[LlmRequest] = []
        self.default_by_purpose: dict[str, Any] = {}
        self.fail_on: set[str] = set()

    def supports_vision(self) -> bool:
        return True

    def model_for(self, purpose: str, *, vision: bool = False) -> str:
        return f"stub-{purpose}{'-vision' if vision else ''}"

    async def complete(self, request: LlmRequest, model: str) -> LlmResponse:
        self.requests.append(request)

        for marker in self.fail_on:
            if marker in request.user:
                raise LlmError(f"stub failure for {marker}", retryable=False)

        payload = None
        for marker, response in self.responses.items():
            if marker in request.user:
                payload = response
                break
        if payload is None:
            payload = self.default_by_purpose.get(request.purpose)
        if payload is None:
            payload = {"facts": []}

        return LlmResponse(
            data=payload,
            raw_text=json.dumps(payload),
            provider=self.name,
            model=model,
            input_tokens=len(request.user) // 4,
            output_tokens=64,
            latency_ms=1,
        )

    def calls_for(self, purpose: str) -> list[LlmRequest]:
        return [request for request in self.requests if request.purpose == purpose]


@pytest.fixture(scope="class")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("layer")


@pytest.fixture(scope="class")
def settings(workspace: Path) -> Settings:
    """Settings for an isolated run, loaded through the real environment path.

    Configured with environment variables rather than by constructing a Settings object, so
    the test exercises the same loading code the application uses and cannot drift from it.

    Scoped to the class so a suite of assertions about one ingest pays for that ingest once.
    Running the pipeline per assertion would make the suite take minutes and discourage
    writing the assertions that make it useful.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("LLM_PROVIDER", "stub")
        patch.setenv("LLM_FALLBACK_PROVIDER", "")
        patch.setenv("LLM_CACHE_ENABLED", "false")
        patch.setenv("LLM_RATE_LIMIT_RPM", "0")
        patch.setenv("LLM_CONCURRENCY", "4")
        patch.setenv("ENABLE_VISION", "true")
        patch.setenv("PARSE_WORKERS", "1")
        patch.setenv("DATA_DIR", str(workspace / "data"))
        patch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{(workspace / 'test.sqlite').as_posix()}")

        reset_settings_cache()
        resolved = get_settings()
        resolved.ensure_directories()
        yield resolved
        reset_settings_cache()


@pytest.fixture(scope="class")
def database(settings: Settings):
    """A fresh SQLite file per test class, with the schema and full-text index built."""
    engine_module.reset_engine()
    engine = engine_module.get_engine()
    init_database(engine)
    yield engine
    engine_module.reset_engine()


@pytest.fixture(scope="class")
def stub() -> StubProvider:
    return StubProvider()
