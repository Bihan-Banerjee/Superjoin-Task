"""Reading and changing configuration from the interface.

Editing settings over HTTP means an endpoint that writes a file and holds API keys, so the
shape of this module is mostly about what it refuses to do.

* **Only a fixed set of keys can be written.** An allow-list, not a pass-through, so a typo
  cannot invent a setting and a caller cannot reach anything not listed here.
* **Secrets are write-only.** A key can be set and cleared, never read back. `GET` reports
  whether one is configured and its last four characters, which is enough to tell two keys
  apart and not enough to use one.
* **Loopback only, unless deliberately opened.** The rest of this API is safe to expose to a
  reader; this part is not, because it can point the pipeline at a different endpoint and
  spend someone else's credit. Remote callers are refused unless `ALLOW_REMOTE_CONFIG` says
  otherwise.
* **Nothing at all in read-only mode.** A deployment serving a snapshot has no business
  holding a key.

The file written is `backend/.env`, which is gitignored. Comments, ordering and any setting
not listed here are preserved, so a hand-written file stays hand-written.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request

from app.config import BACKEND_ROOT, Settings, get_settings, reset_settings_cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])

ENV_PATH = BACKEND_ROOT / ".env"

# Settings the interface may change, and how to describe them to a reader. `secret` marks a
# value that is never sent back. `kind` drives the control the UI renders.
EDITABLE: dict[str, dict[str, Any]] = {
    "GEMINI_API_KEY": {
        "kind": "secret",
        "group": "Model access",
        "label": "Gemini API key",
        "help": "Primary provider. Free tier is enough to ingest the sample corpus.",
    },
    "OPENROUTER_API_KEY": {
        "kind": "secret",
        "group": "Model access",
        "label": "OpenRouter API key",
        "help": "Used when the primary provider is out of quota, if failover is on.",
    },
    "LLM_PROVIDER": {
        "kind": "choice",
        "choices": ["gemini", "openrouter", "ollama", "replay"],
        "group": "Model access",
        "label": "Provider",
        "help": "'replay' answers only from recorded responses and never calls a model.",
    },
    "LLM_FALLBACK_PROVIDER": {
        "kind": "choice",
        "choices": ["", "openrouter", "ollama"],
        "group": "Model access",
        "label": "Failover provider",
        "help": "Tried when the primary rejects a call for quota. Empty disables failover.",
    },
    "LLM_RATE_LIMIT_RPM": {
        "kind": "number",
        "group": "Throughput",
        "label": "Requests per minute",
        "help": "0 disables throttling. Above the free-tier ceiling you will absorb 429s.",
    },
    "LLM_CONCURRENCY": {
        "kind": "number",
        "group": "Throughput",
        "label": "Concurrent requests",
        "help": "How many model calls may be in flight at once.",
    },
    "ENABLE_VISION": {
        "kind": "toggle",
        "group": "Pipeline",
        "label": "Read chart pages as images",
        "help": "Attaches a rendered page image so a stacked bar's colours can be matched.",
    },
    "ENABLE_OCR": {
        "kind": "toggle",
        "group": "Pipeline",
        "label": "OCR scanned pages",
        "help": "Needs Tesseract installed. Scanned pages are detected either way.",
    },
    "ADJUDICATION_CROSS_CHECK": {
        "kind": "toggle",
        "group": "Pipeline",
        "label": "Read ambiguous pairs twice",
        "help": "Swaps the two facts and keeps the verdict only if it survives.",
    },
    "ENABLE_GRAPH_VIEW": {
        "kind": "toggle",
        "group": "Interface",
        "label": "Graph view of relations",
        "help": "A rendering of the same rows the table shows. Off by default on purpose.",
    },
}


def _guard(request: Request) -> Settings:
    """Refuse the request unless this deployment should be answering it at all."""
    settings = get_settings()
    if settings.read_only:
        raise HTTPException(
            status_code=403,
            detail="this deployment is read-only and cannot hold configuration",
        )
    host = (request.client.host if request.client else "") or ""
    local = host in {"127.0.0.1", "::1", "localhost", "testclient"}
    if not local and not settings.allow_remote_config:
        raise HTTPException(
            status_code=403,
            detail=(
                "configuration can only be changed from the machine the server runs on; "
                "set ALLOW_REMOTE_CONFIG=true to override"
            ),
        )
    return settings


def _current_values(settings: Settings) -> dict[str, Any]:
    """What each editable setting is currently set to, with secrets reduced to a hint."""
    raw = {
        "GEMINI_API_KEY": settings.gemini_api_key,
        "OPENROUTER_API_KEY": settings.openrouter_api_key,
        "LLM_PROVIDER": settings.llm_provider,
        "LLM_FALLBACK_PROVIDER": settings.llm_fallback_provider,
        "LLM_RATE_LIMIT_RPM": settings.llm_rate_limit_rpm,
        "LLM_CONCURRENCY": settings.llm_concurrency,
        "ENABLE_VISION": settings.enable_vision,
        "ENABLE_OCR": settings.enable_ocr,
        "ADJUDICATION_CROSS_CHECK": settings.adjudication_cross_check,
        "ENABLE_GRAPH_VIEW": settings.enable_graph_view,
    }
    values: dict[str, Any] = {}
    for name, spec in EDITABLE.items():
        value = raw.get(name)
        if spec["kind"] == "secret":
            text = str(value or "")
            values[name] = {"configured": bool(text), "hint": text[-4:] if text else ""}
        else:
            values[name] = value
    return values


@router.get("")
def read_settings(request: Request) -> dict[str, Any]:
    settings = _guard(request)
    return {
        "fields": [{"name": name, **spec} for name, spec in EDITABLE.items()],
        "values": _current_values(settings),
        "env_path": str(ENV_PATH),
        "warnings": settings.throttle_warnings(),
    }


@router.patch("")
def write_settings(request: Request, changes: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Apply a partial update and rewrite `.env`.

    Changes take effect for work started afterwards. Anything already running keeps the
    settings it began with, which is the honest behaviour: a job that changed provider
    halfway through would be neither reproducible nor explicable.
    """
    # Called for the refusal, not the result: the settings read back below are the ones that
    # exist after the write.
    _guard(request)

    unknown = sorted(set(changes) - set(EDITABLE))
    if unknown:
        raise HTTPException(status_code=400, detail=f"not editable: {', '.join(unknown)}")

    updates: dict[str, str] = {}
    for name, value in changes.items():
        spec = EDITABLE[name]
        if spec["kind"] == "toggle":
            updates[name] = "true" if value else "false"
        elif spec["kind"] == "number":
            # An emptied input is "leave this alone", not an error. Anything else that is
            # not a whole number still is.
            if value is None or str(value).strip() == "":
                continue
            try:
                updates[name] = str(int(str(value).strip()))
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400, detail=f"{name} must be a whole number"
                ) from None
        elif spec["kind"] == "choice":
            text = str(value or "")
            if text not in spec["choices"]:
                raise HTTPException(
                    status_code=400, detail=f"{name} must be one of {spec['choices']}"
                )
            updates[name] = text
        else:
            # A secret. Empty clears it; whitespace is stripped because pasted keys carry it.
            updates[name] = str(value or "").strip()

    _write_env(updates)
    reset_settings_cache()
    refreshed = get_settings()
    logger.info("configuration updated from the interface: %s", ", ".join(sorted(updates)))
    return {
        "values": _current_values(refreshed),
        "warnings": refreshed.throttle_warnings(),
        "provider_configured": bool(
            refreshed.gemini_api_key
            if refreshed.llm_provider == "gemini"
            else refreshed.openrouter_api_key or refreshed.llm_provider in {"ollama", "replay"}
        ),
    }


def _write_env(updates: dict[str, str]) -> None:
    """Update keys in `.env` in place, leaving everything else exactly as it was.

    Rewritten line by line rather than regenerated, so comments, ordering and settings this
    module knows nothing about survive. A key that is not present yet is appended.
    """
    existing = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.is_file() else []
    remaining = dict(updates)
    lines: list[str] = []

    for line in existing:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            name = stripped.split("=", 1)[0].strip()
            if name in remaining:
                lines.append(f"{name}={remaining.pop(name)}")
                continue
        lines.append(line)

    if remaining:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# Added from the configure page")
        lines.extend(f"{name}={value}" for name, value in remaining.items())

    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    # `.env` has no suffix as Path sees it, so with_suffix() would produce
    # ".env.env.tmp". Written beside the target and moved into place, so a crash
    # midway leaves the original intact rather than a half-written file.
    temporary = ENV_PATH.with_name(ENV_PATH.name + ".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(ENV_PATH)
