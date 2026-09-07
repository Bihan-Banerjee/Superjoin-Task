"""Fact extraction and document profiling.

Turns a parsed page into candidate facts. Two things here are worth more than the prompt
itself.

Pages are batched by cost, not by count. A hundred-page filing has a few dense financial
statement pages and a long tail of sparse ones, and sending each sparse page as its own
request wastes most of the latency budget on overhead. Batching by character budget keeps
requests uniformly sized whatever the document looks like.

Failure is attributed, never swallowed. If a page's call fails after its retries, that page
is recorded as failed against the document. It does not quietly return zero facts, because
a page that produced nothing and a page that was never successfully read look identical in
the output otherwise, and only one of them is a real finding.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import Settings
from app.core.text import truncate
from app.llm.base import PURPOSE_EXTRACT, PURPOSE_PROFILE, LlmError, LlmRequest
from app.llm.client import LlmClient
from app.llm.prompts import (
    EXTRACT_SYSTEM,
    PROFILE_SYSTEM,
    extract_prompt,
    profile_prompt,
)
from app.llm.schemas import DOCUMENT_PROFILE_SCHEMA, FACT_EXTRACTION_SCHEMA
from app.pipeline.classify import PageSignals, is_vision_candidate
from app.pipeline.parse import ParsedDocument, ParsedPage, render_page_png

logger = logging.getLogger(__name__)

# Characters of page content per request. Sized so a request stays well inside the output
# limit even when a page turns out to be unusually fact-dense.
BATCH_CHARACTER_BUDGET = 9000
MAX_PAGES_PER_BATCH = 4
PROFILE_EXCERPT_CHARACTERS = 12000
EXTRACTOR_VERSION = "extract-1"


@dataclass
class PageResult:
    page_number: int
    facts: list[dict[str, Any]] = field(default_factory=list)
    unattributed: list[dict[str, Any]] = field(default_factory=list)
    model: str | None = None
    used_vision: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


async def profile_document(
    client: LlmClient,
    parsed: ParsedDocument,
    renditions: dict[int, str],
) -> dict[str, Any]:
    """Read the front matter once to establish the conventions every fact inherits.

    The excerpt is drawn from the opening pages plus any page that declares a scale, because
    the "(All amounts in Indian Rupees in million)" note usually sits above a financial
    statement two thirds of the way through rather than on the cover.
    """
    excerpt_pages = [page for page in parsed.pages[:6]]
    declaring = [
        page
        for page in parsed.pages
        if (page.unit_scale or page.unit_currency) and page not in excerpt_pages
    ][:3]

    parts: list[str] = []
    for page in [*excerpt_pages, *declaring]:
        content = renditions.get(page.page_number) or page.text
        parts.append(f"[page {page.page_number}]\n{truncate(content, 2600)}")
    excerpt = truncate("\n\n".join(parts), PROFILE_EXCERPT_CHARACTERS)

    request = LlmRequest(
        purpose=PURPOSE_PROFILE,
        system=PROFILE_SYSTEM,
        user=profile_prompt(parsed.path.name, parsed.metadata, excerpt),
        schema=DOCUMENT_PROFILE_SCHEMA,
        max_output_tokens=2048,
    )

    profile: dict[str, Any] = {}
    try:
        response = await client.complete(request)
        if isinstance(response.data, dict):
            profile = response.data
    except LlmError as error:
        # A failed profile costs context, not the document. Scale and currency declarations
        # are read from the page text below either way, and periods fall back to the
        # calendar convention, so continuing produces a degraded result rather than none.
        logger.warning("could not profile %s, continuing without it: %s", parsed.path.name, error)

    # A scale declared in the page text is observed rather than inferred, so it wins over
    # the model's reading of the front matter.
    observed = _observed_defaults(parsed)
    for key, value in observed.items():
        if value and not profile.get(key):
            profile[key] = value
    return profile


def _observed_defaults(parsed: ParsedDocument) -> dict[str, Any]:
    from app.core.units import SCALES

    currencies = [page.unit_currency for page in parsed.pages if page.unit_currency]
    scales = [page.unit_scale for page in parsed.pages if page.unit_scale]
    result: dict[str, Any] = {}
    if currencies:
        result["default_currency"] = max(set(currencies), key=currencies.count)
    if scales:
        common = max(set(scales), key=scales.count)
        for name, factor in SCALES.items():
            if factor == common and len(name) > 2:
                result["default_scale"] = name
                break
    return result


@dataclass
class _Batch:
    pages: list[ParsedPage]
    signals: dict[int, PageSignals]
    renditions: dict[int, str]
    vision_page: ParsedPage | None = None


def plan_batches(
    pages: list[ParsedPage],
    signals: dict[int, PageSignals],
    renditions: dict[int, str],
    *,
    vision_enabled: bool,
) -> list[_Batch]:
    """Group pages into requests by content size.

    A page needing an image is always sent alone: the image is what the model has to reason
    over, and mixing three other pages into that request only invites it to confuse them.
    """
    batches: list[_Batch] = []
    current: list[ParsedPage] = []
    budget = 0

    def flush() -> None:
        nonlocal current, budget
        if current:
            batches.append(_Batch(pages=current, signals=signals, renditions=renditions))
            current = []
            budget = 0

    for page in pages:
        signal = signals[page.page_number]
        if not signal.should_extract:
            continue

        rendition = renditions.get(page.page_number, "")
        if not rendition.strip():
            continue

        if vision_enabled and is_vision_candidate(signal.page_type, page):
            flush()
            batches.append(
                _Batch(pages=[page], signals=signals, renditions=renditions, vision_page=page)
            )
            continue

        size = len(rendition)
        if current and (
            budget + size > BATCH_CHARACTER_BUDGET or len(current) >= MAX_PAGES_PER_BATCH
        ):
            flush()
        current.append(page)
        budget += size

    flush()
    return batches


async def extract_document(
    client: LlmClient,
    parsed: ParsedDocument,
    signals: dict[int, PageSignals],
    renditions: dict[int, str],
    document_context: str,
    settings: Settings,
    *,
    on_progress=None,
) -> dict[int, PageResult]:
    """Extract every extractable page, with bounded concurrency."""
    vision_enabled = settings.enable_vision and client.supports_vision()
    batches = plan_batches(parsed.pages, signals, renditions, vision_enabled=vision_enabled)
    if not batches:
        return {}

    results: dict[int, PageResult] = {}
    completed = 0
    lock = asyncio.Lock()

    async def run(batch: _Batch) -> None:
        nonlocal completed
        outcome = await _extract_batch(client, parsed.path, batch, document_context, settings)
        async with lock:
            results.update(outcome)
            completed += 1
            if on_progress is not None:
                on_progress(completed, len(batches))

    # The client already bounds in-flight calls; gathering everything here lets it fill that
    # window without the orchestrator needing its own scheduler.
    await asyncio.gather(*(run(batch) for batch in batches))
    return results


async def _extract_batch(
    client: LlmClient,
    pdf_path: Path,
    batch: _Batch,
    document_context: str,
    settings: Settings,
) -> dict[int, PageResult]:
    images: list[bytes] = []
    if batch.vision_page is not None:
        try:
            images = [
                render_page_png(
                    pdf_path, batch.vision_page.page_number, dpi=settings.vision_render_dpi
                )
            ]
        except Exception as error:
            logger.warning(
                "could not render page %d for vision, continuing with text: %s",
                batch.vision_page.page_number,
                error,
            )

    sections: list[str] = []
    for page in batch.pages:
        signal = batch.signals[page.page_number]
        sections.append(
            extract_prompt(
                document_context=document_context,
                page_number=page.page_number,
                printed_label=page.printed_label,
                page_type=signal.page_type,
                page_unit_note=_page_unit_note(page),
                rendition=batch.renditions.get(page.page_number, ""),
                has_image=bool(images),
            )
        )

    request = LlmRequest(
        purpose=PURPOSE_EXTRACT,
        system=EXTRACT_SYSTEM + _MULTI_PAGE_NOTE if len(batch.pages) > 1 else EXTRACT_SYSTEM,
        user="\n\n".join(sections),
        schema=_batch_schema(len(batch.pages)),
        images=images,
        max_output_tokens=8192,
    )

    numbers = [page.page_number for page in batch.pages]
    try:
        response = await client.complete(request)
    except LlmError as error:
        logger.warning("extraction failed for pages %s: %s", numbers, error)
        return {number: PageResult(page_number=number, error=str(error)) for number in numbers}

    return _split_response(response.data, numbers, response.model, bool(images))


_MULTI_PAGE_NOTE = """

Several pages follow. Each fact must carry the page_number of the page it came from, and \
its evidence_quote must come from that same page."""


def _batch_schema(page_count: int) -> dict[str, Any]:
    if page_count <= 1:
        return FACT_EXTRACTION_SCHEMA
    schema = {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "items": dict(FACT_EXTRACTION_SCHEMA["properties"]["facts"]["items"]),
            },
            "unattributed": dict(FACT_EXTRACTION_SCHEMA["properties"]["unattributed"]),
        },
        "required": ["facts"],
    }
    for container in (
        schema["properties"]["facts"]["items"],
        schema["properties"]["unattributed"]["items"],
    ):
        container["properties"] = dict(container["properties"])
        container["properties"]["page_number"] = {
            "type": "integer",
            "description": "The page this came from, as labelled in the prompt.",
        }
        container["required"] = [*container.get("required", []), "page_number"]
    return schema


def _split_response(
    data: Any, page_numbers: list[int], model: str, used_vision: bool
) -> dict[int, PageResult]:
    results = {
        number: PageResult(page_number=number, model=model, used_vision=used_vision)
        for number in page_numbers
    }
    if not isinstance(data, dict):
        return results

    default_page = page_numbers[0]
    for item in data.get("facts") or []:
        if not isinstance(item, dict):
            continue
        target = _page_of(item, page_numbers, default_page)
        results[target].facts.append(item)

    for item in data.get("unattributed") or []:
        if not isinstance(item, dict):
            continue
        target = _page_of(item, page_numbers, default_page)
        results[target].unattributed.append(item)

    return results


def _page_of(item: dict[str, Any], page_numbers: list[int], default: int) -> int:
    raw = item.pop("page_number", None)
    try:
        number = int(raw)
    except (TypeError, ValueError):
        return default
    return number if number in page_numbers else default


def _page_unit_note(page: ParsedPage) -> str:
    if not page.unit_scale and not page.unit_currency:
        return ""
    from app.core.units import SCALES

    scale_name = ""
    if page.unit_scale:
        for name, factor in SCALES.items():
            if factor == page.unit_scale and len(name) > 2:
                scale_name = name
                break
    parts = [part for part in (page.unit_currency, scale_name) if part]
    if not parts:
        return ""
    return (
        f"This page declares that its figures are stated in {' '.join(parts)}. "
        "Apply that to values on this page that do not carry their own unit, and say so "
        "in the unit field."
    )


def build_document_context(profile: dict[str, Any], parsed: ParsedDocument) -> str:
    """The context block prefixed to every extraction prompt for this document."""
    lines = [
        f"  title: {profile.get('title') or parsed.path.stem}",
        f"  publisher: {profile.get('publisher') or 'not stated'}",
        f"  type: {profile.get('doc_type') or 'unknown'}",
        f"  about: {profile.get('subject_entity') or 'not stated'}",
        f"  period covered: {profile.get('period_label') or 'not stated'}",
        f"  as of: {profile.get('as_of_date') or 'not stated'}",
        f"  fiscal year convention: {profile.get('fiscal_convention') or 'calendar'}",
    ]
    currency = profile.get("default_currency")
    scale = profile.get("default_scale")
    if currency or scale:
        lines.append(
            f"  figures default to: {' '.join(part for part in (currency, scale) if part)}"
        )
    if profile.get("reporting_basis"):
        lines.append(f"  reporting basis: {profile['reporting_basis']}")
    if profile.get("notes"):
        lines.append(f"  note: {truncate(str(profile['notes']), 300)}")
    return "\n".join(lines)
