"""Concrete providers: Gemini, any OpenAI-compatible endpoint, and Ollama.

Each one is a thin translation of `LlmRequest` into that vendor's wire format and back.
Where a provider supports constrained decoding against a JSON schema it is used, because
it removes a whole class of parse failures; where it does not, the schema is described in
the prompt and the response is parsed defensively.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any

import httpx

from app.config import Settings
from app.llm.base import (
    PURPOSE_ADJUDICATE,
    PURPOSE_EXTRACT,
    LlmError,
    LlmRequest,
    LlmResponse,
    Provider,
    extract_json,
)

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}


def _raise_for_status(response: httpx.Response, provider: str) -> None:
    if response.status_code < 400:
        return
    body = response.text[:400]
    raise LlmError(
        f"{provider} returned {response.status_code}: {body}",
        retryable=response.status_code in _RETRYABLE_STATUS,
    )


class GeminiProvider(Provider):
    name = "gemini"
    _BASE = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, settings: Settings) -> None:
        if not settings.gemini_api_key:
            raise LlmError("GEMINI_API_KEY is not set")
        self._key = settings.gemini_api_key
        self._settings = settings
        self._client = httpx.AsyncClient(timeout=settings.llm_timeout_seconds)

    def supports_vision(self) -> bool:
        return True

    def model_for(self, purpose: str, *, vision: bool = False) -> str:
        if vision:
            return self._settings.gemini_vision_model
        if purpose == PURPOSE_ADJUDICATE:
            return self._settings.gemini_adjudication_model
        return self._settings.gemini_extraction_model

    async def complete(self, request: LlmRequest, model: str) -> LlmResponse:
        parts: list[dict[str, Any]] = [{"text": request.user}]
        for image in request.images:
            parts.append(
                {
                    "inline_data": {
                        "mime_type": "image/png",
                        "data": base64.b64encode(image).decode("ascii"),
                    }
                }
            )

        payload = {
            "systemInstruction": {"parts": [{"text": request.system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_output_tokens,
                "responseMimeType": "application/json",
                "responseSchema": _to_gemini_schema(request.schema),
            },
        }

        started = time.perf_counter()
        response = await self._client.post(
            f"{self._BASE}/models/{model}:generateContent",
            params={"key": self._key},
            json=payload,
        )
        _raise_for_status(response, self.name)
        body = response.json()

        candidates = body.get("candidates") or []
        if not candidates:
            reason = (body.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise LlmError(f"gemini returned no candidates ({reason})", retryable=False)

        candidate = candidates[0]
        finish = candidate.get("finishReason")
        text = "".join(
            part.get("text", "") for part in (candidate.get("content") or {}).get("parts", [])
        )
        if finish == "MAX_TOKENS" and not text.rstrip().endswith(("}", "]")):
            raise LlmError("gemini response was truncated by the output limit", retryable=False)

        usage = body.get("usageMetadata") or {}
        return LlmResponse(
            data=extract_json(text),
            raw_text=text,
            provider=self.name,
            model=model,
            input_tokens=int(usage.get("promptTokenCount", 0)),
            output_tokens=int(usage.get("candidatesTokenCount", 0)),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class OpenAICompatibleProvider(Provider):
    """OpenRouter, and any other endpoint speaking the OpenAI chat completions API."""

    name = "openrouter"

    def __init__(self, settings: Settings) -> None:
        if not settings.openrouter_api_key:
            raise LlmError("OPENROUTER_API_KEY is not set")
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1",
            timeout=settings.llm_timeout_seconds,
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "X-Title": "Fact Knowledge Layer",
            },
        )

    def supports_vision(self) -> bool:
        return True

    def model_for(self, purpose: str, *, vision: bool = False) -> str:
        if vision:
            return self._settings.openrouter_vision_model
        if purpose == PURPOSE_ADJUDICATE:
            return self._settings.openrouter_adjudication_model
        return self._settings.openrouter_extraction_model

    async def complete(self, request: LlmRequest, model: str) -> LlmResponse:
        content: list[dict[str, Any]] = [{"type": "text", "text": request.user}]
        for image in request.images:
            encoded = base64.b64encode(image).decode("ascii")
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}}
            )

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": content},
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "strict": False,
                    "schema": request.schema,
                },
            },
        }

        started = time.perf_counter()
        response = await self._client.post("/chat/completions", json=payload)
        _raise_for_status(response, self.name)
        body = response.json()

        if "error" in body and not body.get("choices"):
            message = str((body.get("error") or {}).get("message", "unknown error"))
            raise LlmError(f"openrouter error: {message}", retryable="rate" in message.lower())

        choices = body.get("choices") or []
        if not choices:
            raise LlmError("openrouter returned no choices", retryable=True)
        text = (choices[0].get("message") or {}).get("content") or ""

        usage = body.get("usage") or {}
        return LlmResponse(
            data=extract_json(text),
            raw_text=text,
            provider=self.name,
            model=model,
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class OllamaProvider(Provider):
    """Fully local inference. No credentials, no network egress, lower accuracy."""

    name = "ollama"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.ollama_host.rstrip("/"),
            timeout=settings.llm_timeout_seconds,
        )

    def supports_vision(self) -> bool:
        # Depends entirely on the pulled model, so the pipeline is told no and the text
        # path is used. Claiming vision and silently ignoring the image would be worse.
        return False

    def model_for(self, purpose: str, *, vision: bool = False) -> str:
        if purpose == PURPOSE_ADJUDICATE:
            return self._settings.ollama_adjudication_model
        return self._settings.ollama_extraction_model

    async def complete(self, request: LlmRequest, model: str) -> LlmResponse:
        payload = {
            "model": model,
            "prompt": request.user,
            "system": request.system,
            "stream": False,
            "format": request.schema,
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_output_tokens,
            },
        }

        started = time.perf_counter()
        response = await self._client.post("/api/generate", json=payload)
        _raise_for_status(response, self.name)
        body = response.json()
        text = body.get("response") or ""

        return LlmResponse(
            data=extract_json(text),
            raw_text=text,
            provider=self.name,
            model=model,
            input_tokens=int(body.get("prompt_eval_count", 0)),
            output_tokens=int(body.get("eval_count", 0)),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class ReplayProvider(Provider):
    """Serves previously recorded responses and refuses to invent new ones.

    This is what lets someone evaluate the project without credentials: the recorded
    responses in `backend/seed/replay` replay the exact pipeline offline. A request with no
    recording fails loudly rather than fabricating output, so a replay run cannot quietly
    diverge from the run it is reproducing.
    """

    name = "replay"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def supports_vision(self) -> bool:
        return True

    def model_for(self, purpose: str, *, vision: bool = False) -> str:
        return "replay"

    async def complete(self, request: LlmRequest, model: str) -> LlmResponse:
        raise LlmError(
            "no recorded response for this request; replay mode cannot call a model",
            retryable=False,
        )


_BUILDERS = {
    "gemini": GeminiProvider,
    "openrouter": OpenAICompatibleProvider,
    "ollama": OllamaProvider,
    "replay": ReplayProvider,
}


def build_provider(name: str, settings: Settings) -> Provider:
    key = (name or "").strip().lower()
    builder = _BUILDERS.get(key)
    if builder is None:
        raise LlmError(f"unknown LLM provider '{name}'; expected one of {sorted(_BUILDERS)}")
    return builder(settings)


def _to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Translate a JSON Schema into the subset Gemini accepts.

    Gemini rejects `additionalProperties`, `$schema` and a few other standard keywords, and
    wants types upper-cased. Sending the schema unmodified fails the whole request, so it is
    rewritten here rather than requiring every prompt to be written twice.
    """
    if not isinstance(schema, dict):
        return {"type": "STRING"}

    allowed = {
        "type",
        "format",
        "description",
        "nullable",
        "enum",
        "items",
        "properties",
        "required",
        "propertyOrdering",
    }
    result: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in allowed:
            continue
        if key == "type" and isinstance(value, str):
            result["type"] = value.upper()
        elif key == "items":
            result["items"] = _to_gemini_schema(value)
        elif key == "properties" and isinstance(value, dict):
            result["properties"] = {name: _to_gemini_schema(child) for name, child in value.items()}
        else:
            result[key] = value

    if result.get("type") == "OBJECT" and "properties" not in result:
        result["properties"] = {}
    return result


__all__ = [
    "GeminiProvider",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "ReplayProvider",
    "build_provider",
    "PURPOSE_EXTRACT",
]
