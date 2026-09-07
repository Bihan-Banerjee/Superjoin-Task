"""Language model access.

One interface, several providers. The pipeline never names a vendor: it asks for a
completion with a JSON schema and gets structured data back, or an error it can record.

Three things this layer is responsible for beyond dispatching a request:

* **Determinism.** Temperature is zero and every response is cached by a hash of the exact
  request. Re-running an ingest costs nothing and produces the same knowledge layer, which
  is what makes the results reproducible for anyone evaluating the project.
* **Honesty about failure.** A call that fails after its retries is recorded as a failure
  against the page it was for. It never silently yields an empty result that would look
  like "this page had no facts".
* **Cost control.** Concurrency, request rate and a per-job call ceiling are enforced here
  rather than in each caller.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.core.hashing import stable_key

logger = logging.getLogger(__name__)

PURPOSE_PROFILE = "profile"
PURPOSE_EXTRACT = "extract"
PURPOSE_ADJUDICATE = "adjudicate"
PURPOSE_REGISTRY = "registry"


class LlmError(RuntimeError):
    """A model call that could not be completed."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class BudgetExceeded(LlmError):
    def __init__(self, limit: int) -> None:
        super().__init__(f"model call budget of {limit} reached for this job", retryable=False)


@dataclass
class LlmRequest:
    purpose: str
    system: str
    user: str
    schema: dict[str, Any]
    model: str | None = None
    images: list[bytes] = field(default_factory=list)
    max_output_tokens: int = 8192
    temperature: float = 0.0

    def cache_key(self, provider: str, model: str) -> str:
        return stable_key(
            {
                "provider": provider,
                "model": model,
                "system": self.system,
                "user": self.user,
                "schema": self.schema,
                "temperature": self.temperature,
                "max_output_tokens": self.max_output_tokens,
                # Images participate in the key by digest so a re-render of the same page
                # hits the cache, while a different page never can.
                "images": [stable_key(image.hex()) for image in self.images],
            }
        )


@dataclass
class LlmResponse:
    data: Any
    raw_text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    cached: bool = False
    attempts: int = 1


class Provider(ABC):
    name: str = "provider"

    @abstractmethod
    def model_for(self, purpose: str, *, vision: bool = False) -> str: ...

    @abstractmethod
    async def complete(self, request: LlmRequest, model: str) -> LlmResponse: ...

    @abstractmethod
    def supports_vision(self) -> bool: ...

    async def aclose(self) -> None:
        return None


class RateLimiter:
    """Token bucket over a rolling minute, shared by every worker on a job."""

    def __init__(self, requests_per_minute: int) -> None:
        self._limit = requests_per_minute
        self._times: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if self._limit <= 0:
            return
        while True:
            async with self._lock:
                now = time.monotonic()
                self._times = [stamp for stamp in self._times if now - stamp < 60.0]
                if len(self._times) < self._limit:
                    self._times.append(now)
                    return
                wait = 60.0 - (now - self._times[0]) + 0.01
            await asyncio.sleep(max(wait, 0.05))


def extract_json(text: str) -> Any:
    """Parse a model response into JSON, tolerating the wrappers models add.

    Providers that accept a response schema usually return clean JSON, but not always, and
    local models via Ollama rarely do. Rather than fail the page, peel off code fences and
    fall back to the outermost balanced object or array.
    """
    if not text or not text.strip():
        raise LlmError("model returned an empty response", retryable=True)

    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.split("\n", 1)[-1] if "\n" in candidate else candidate
        candidate = candidate.rsplit("```", 1)[0]
        if candidate.lstrip().startswith(("json", "JSON")):
            candidate = candidate.lstrip()[4:]
        candidate = candidate.strip()

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    sliced = _outermost_json(candidate)
    if sliced is None:
        raise LlmError("model response was not JSON", retryable=True)
    try:
        return json.loads(sliced)
    except json.JSONDecodeError as error:
        raise LlmError(f"model response was not valid JSON: {error}", retryable=True) from error


def _outermost_json(text: str) -> str | None:
    start = min(
        (index for index in (text.find("{"), text.find("[")) if index >= 0),
        default=-1,
    )
    if start < 0:
        return None

    opening = text[start]
    closing = "}" if opening == "{" else "]"
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None
