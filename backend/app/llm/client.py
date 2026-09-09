"""The client the pipeline actually calls.

Wraps a provider with the concerns that would otherwise be duplicated at every call site:
an on-disk response cache, bounded concurrency, request rate limiting, retries with
backoff, failover to a second provider, a per-job call ceiling, and per-call accounting.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.llm.base import (
    PURPOSE_ADJUDICATE,
    BudgetExceeded,
    LlmError,
    LlmRequest,
    LlmResponse,
    Provider,
    RateLimiter,
)
from app.llm.providers import build_provider

logger = logging.getLogger(__name__)

# How long to wait after a quota rejection. Sized for a per-minute allowance.
RATE_LIMIT_BACKOFF_SECONDS = 62.0

# Consecutive quota rejections after which a model is treated as spent for this job. Free
# tiers impose a daily cap as well as a per-minute one, and no amount of waiting clears the
# daily one. Without this, every remaining call spends four minutes backing off before
# failing anyway: an ingest that looks hung rather than one that reports a problem.
QUOTA_FAILURES_BEFORE_GIVING_UP = 3


@dataclass
class CallRecord:
    purpose: str
    provider: str
    model: str
    prompt_sha: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cached: bool
    attempts: int
    ok: bool
    error: str | None = None


@dataclass
class UsageLedger:
    """Accumulates what a job spent, so the evaluation page can report it."""

    records: list[CallRecord] = field(default_factory=list)

    def add(self, record: CallRecord) -> None:
        self.records.append(record)

    @property
    def calls(self) -> int:
        return len(self.records)

    @property
    def cache_hits(self) -> int:
        return sum(1 for record in self.records if record.cached)

    @property
    def failures(self) -> int:
        return sum(1 for record in self.records if not record.ok)

    @property
    def input_tokens(self) -> int:
        return sum(record.input_tokens for record in self.records)

    @property
    def output_tokens(self) -> int:
        return sum(record.output_tokens for record in self.records)

    def summary(self) -> dict[str, Any]:
        billed = self.calls - self.cache_hits
        return {
            "calls": self.calls,
            "billed_calls": billed,
            "cache_hits": self.cache_hits,
            "cache_hit_rate": round(self.cache_hits / self.calls, 3) if self.calls else 0.0,
            "failures": self.failures,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


class ResponseCache:
    """Content-addressed cache of model responses, one JSON file per request.

    Keyed by a hash of the provider, model, prompts, schema and any images, so a cache hit
    is only ever an exact repeat. Re-ingesting a document after a code change to a later
    stage therefore costs nothing, which matters a great deal when iterating on the
    reconciliation logic over a corpus this size.
    """

    def __init__(self, directory: Path, *, enabled: bool = True) -> None:
        self._directory = directory
        self._enabled = enabled
        if enabled:
            directory.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Shard by the first two characters; a flat directory of tens of thousands of files
        # is slow to list on Windows.
        return self._directory / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        if not self._enabled:
            return None
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def put(self, key: str, payload: dict[str, Any]) -> None:
        if not self._enabled:
            return
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
        except OSError as error:
            logger.warning("could not write cache entry %s: %s", key, error)


class LlmClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        provider: Provider | None = None,
        fallback: Provider | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._provider = provider
        self._fallback = fallback
        self._fallback_requested = bool(self._settings.llm_fallback_provider)
        self._semaphore = asyncio.Semaphore(self._settings.llm_concurrency)
        # One limiter per model rather than one for the client. Quotas are enforced per
        # model, and the two this pipeline uses differ by a factor of three: extraction runs
        # on a model with free-tier headroom, adjudication on a stronger one that rejects
        # above five requests a minute. A single shared budget either throttles extraction to
        # the slower model's limit or drives adjudication into a 429 on every call.
        self._limiters: dict[str, RateLimiter] = {}
        self._quota_failures: dict[str, int] = {}
        self._caches = [
            # Recorded responses ship with the repository and are read-only; the runtime
            # cache is written to as the pipeline goes.
            ResponseCache(self._settings.replay_dir, enabled=True),
            ResponseCache(self._settings.cache_dir, enabled=self._settings.llm_cache_enabled),
        ]
        self._ledger = UsageLedger()
        self._budget = self._settings.llm_max_calls_per_job
        self._spent = 0
        self._lock = asyncio.Lock()

    @property
    def ledger(self) -> UsageLedger:
        return self._ledger

    @property
    def provider_name(self) -> str:
        return self._resolve_provider().name

    def _resolve_provider(self) -> Provider:
        if self._provider is None:
            self._provider = build_provider(self._settings.llm_provider, self._settings)
        return self._provider

    def _resolve_fallback(self) -> Provider | None:
        if not self._fallback_requested:
            return None
        if self._fallback is None:
            try:
                self._fallback = build_provider(
                    self._settings.llm_fallback_provider, self._settings
                )
            except LlmError as error:
                logger.warning("fallback provider unavailable: %s", error)
                self._fallback_requested = False
                return None
        return self._fallback

    def _note_quota_failure(self, model: str) -> None:
        self._quota_failures[model] = self._quota_failures.get(model, 0) + 1
        if self._quota_failures[model] == QUOTA_FAILURES_BEFORE_GIVING_UP:
            logger.error(
                "%s has rejected %d consecutive calls for quota; treating it as exhausted "
                "for the rest of this job",
                model,
                QUOTA_FAILURES_BEFORE_GIVING_UP,
            )

    def _quota_exhausted(self, model: str) -> bool:
        return self._quota_failures.get(model, 0) >= QUOTA_FAILURES_BEFORE_GIVING_UP

    def _limiter_for(self, purpose: str, model: str) -> RateLimiter:
        limiter = self._limiters.get(model)
        if limiter is None:
            rpm = (
                self._settings.llm_adjudication_rate_limit_rpm
                if purpose == PURPOSE_ADJUDICATE
                else self._settings.llm_rate_limit_rpm
            )
            limiter = RateLimiter(rpm)
            self._limiters[model] = limiter
        return limiter

    def supports_vision(self) -> bool:
        try:
            return self._resolve_provider().supports_vision()
        except LlmError:
            return False

    async def complete(self, request: LlmRequest) -> LlmResponse:
        provider = self._resolve_provider()
        model = request.model or provider.model_for(request.purpose, vision=bool(request.images))
        key = request.cache_key(provider.name, model)

        for cache in self._caches:
            hit = cache.get(key)
            if hit is not None:
                response = LlmResponse(
                    data=hit.get("data"),
                    raw_text=hit.get("raw_text", ""),
                    provider=hit.get("provider", provider.name),
                    model=hit.get("model", model),
                    input_tokens=int(hit.get("input_tokens", 0)),
                    output_tokens=int(hit.get("output_tokens", 0)),
                    latency_ms=0,
                    cached=True,
                )
                self._record(request, response, key, ok=True)
                return response

        await self._reserve_budget()
        async with self._semaphore:
            response = await self._call_with_retries(provider, request, model, key)

        self._caches[-1].put(
            key,
            {
                "data": response.data,
                "raw_text": response.raw_text,
                "provider": response.provider,
                "model": response.model,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
            },
        )
        return response

    async def _reserve_budget(self) -> None:
        if not self._budget:
            return
        async with self._lock:
            if self._spent >= self._budget:
                raise BudgetExceeded(self._budget)
            self._spent += 1

    async def _call_with_retries(
        self, provider: Provider, request: LlmRequest, model: str, key: str
    ) -> LlmResponse:
        attempts = self._settings.llm_max_attempts
        last_error: LlmError | None = None
        candidates: list[tuple[Provider, str]] = [(provider, model)]

        fallback = self._resolve_fallback()
        if fallback is not None:
            candidates.append(
                (fallback, fallback.model_for(request.purpose, vision=bool(request.images)))
            )

        for provider_index, (candidate, candidate_model) in enumerate(candidates):
            for attempt in range(1, attempts + 1):
                if self._quota_exhausted(candidate_model):
                    last_error = LlmError(
                        f"{candidate_model} is out of quota for this job", retryable=False
                    )
                    break
                try:
                    await self._limiter_for(request.purpose, candidate_model).acquire()
                    response = await candidate.complete(request, candidate_model)
                    response.attempts = attempt
                    self._quota_failures.pop(candidate_model, None)
                    self._record(request, response, key, ok=True)
                    return response
                except LlmError as error:
                    last_error = error
                    if error.rate_limited:
                        self._note_quota_failure(candidate_model)
                    if not error.retryable or attempt == attempts:
                        break
                    if error.rate_limited:
                        if self._quota_exhausted(candidate_model):
                            break
                        # Wait out the quota window rather than spending the remaining
                        # attempts inside it.
                        delay = RATE_LIMIT_BACKOFF_SECONDS * (0.75 + random.random() * 0.5)
                    else:
                        # Full jitter: several workers hitting the same limit must not retry
                        # in lockstep, or they simply collide again.
                        delay = min(2 ** (attempt - 1), 20) * (0.5 + random.random() * 0.5)
                    logger.warning(
                        "%s call failed (attempt %d/%d), retrying in %.1fs: %s",
                        candidate.name,
                        attempt,
                        attempts,
                        delay,
                        error,
                    )
                    await asyncio.sleep(delay)
                except (TimeoutError, OSError) as error:
                    last_error = LlmError(str(error), retryable=True)
                    if attempt == attempts:
                        break
                    await asyncio.sleep(min(2**attempt, 20))

            if provider_index + 1 < len(candidates):
                logger.warning(
                    "falling back from %s to %s after: %s",
                    candidate.name,
                    candidates[provider_index + 1][0].name,
                    last_error,
                )

        failure = last_error or LlmError("model call failed for an unknown reason")
        self._record(
            request,
            LlmResponse(
                data=None,
                raw_text="",
                provider=provider.name,
                model=model,
                attempts=attempts,
            ),
            key,
            ok=False,
            error=str(failure),
        )
        raise failure

    def _record(
        self,
        request: LlmRequest,
        response: LlmResponse,
        key: str,
        *,
        ok: bool,
        error: str | None = None,
    ) -> None:
        self._ledger.add(
            CallRecord(
                purpose=request.purpose,
                provider=response.provider,
                model=response.model,
                prompt_sha=key,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                latency_ms=response.latency_ms,
                cached=response.cached,
                attempts=response.attempts,
                ok=ok,
                error=error,
            )
        )

    async def aclose(self) -> None:
        for provider in (self._provider, self._fallback):
            if provider is not None:
                await provider.aclose()
