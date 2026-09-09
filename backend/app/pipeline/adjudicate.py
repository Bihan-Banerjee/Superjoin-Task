"""Model adjudication for pairs the rules could not settle.

Reached only when the deterministic reconciler declines. Two things make this useful rather
than a coin flip.

The model is given evidence, not summaries. Both verbatim quotes plus the surrounding text
from their pages, because the distinction that explains a difference is very often a phrase
just outside what was extracted: a column header, a footnote, a bracketed "(revised)".

The model is told what the mechanical comparison already established, so it is answering
the narrow question that remains rather than starting over and possibly disagreeing with
arithmetic that is not in doubt.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.text import expand_to_context, truncate
from app.db.models import (
    DECIDED_BY_MODEL,
    DIM_NONE,
    REL_CONTRADICTS,
    REL_CORROBORATES,
    REL_RECONCILED,
    REL_REFINES,
    Document,
    Fact,
    Page,
)
from app.llm.base import PURPOSE_ADJUDICATE, LlmError, LlmRequest
from app.llm.client import LlmClient
from app.llm.prompts import ADJUDICATE_SYSTEM, adjudicate_prompt
from app.llm.schemas import ADJUDICATION_SCHEMA, DIMENSIONS, VERDICTS
from app.pipeline.reconcile import Verdict, severity_for

logger = logging.getLogger(__name__)

CONTEXT_RADIUS = 420

# What a verdict is worth once it turns out to depend on which fact came first. Not zero:
# the model did read the evidence and did reach a conclusion. Not what an unchallenged
# verdict is worth either, since half the time it reached a different one.
ORDER_SENSITIVE_CONFIDENCE = 0.55

_VERDICT_MAP = {
    "corroborates": REL_CORROBORATES,
    "contradicts": REL_CONTRADICTS,
    "reconciled_by_context": REL_RECONCILED,
    "refines": REL_REFINES,
}


@dataclass
class CrossCheckStats:
    """What the second opinion cost and what it caught."""

    checked: int = 0
    agreed: int = 0
    order_sensitive: int = 0
    dropped: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "agreed": self.agreed,
            "order_sensitive": self.order_sensitive,
            "dropped": self.dropped,
            "agreement_rate": round(self.agreed / self.checked, 3) if self.checked else 0.0,
        }


@dataclass
class AdjudicationRequest:
    left: Fact
    right: Fact
    note: str
    delta_absolute: float | None
    delta_relative: float | None


async def adjudicate_all(
    client: LlmClient,
    requests: list[AdjudicationRequest],
    context: dict[int, dict[str, Any]],
    *,
    on_progress=None,
    cross_check: bool = False,
    stats: CrossCheckStats | None = None,
) -> dict[tuple[int, int], Verdict]:
    """Adjudicate every escalated pair.

    Context is passed in rather than loaded here so the caller can release its database
    session before this runs: these are the slowest calls in the pipeline, and holding a
    transaction open across them blocks every other writer.
    """
    if not requests:
        return {}

    results: dict[tuple[int, int], Verdict] = {}
    completed = 0
    lock = asyncio.Lock()
    tally = stats if stats is not None else CrossCheckStats()

    async def run(item: AdjudicationRequest) -> None:
        nonlocal completed
        verdict = await _adjudicate_pair(client, item, context, cross_check, tally)
        async with lock:
            if verdict is not None:
                results[(item.left.id, item.right.id)] = verdict
            completed += 1
            if on_progress is not None:
                on_progress(completed, len(requests))

    await asyncio.gather(*(run(item) for item in requests))
    return results


async def _adjudicate_pair(
    client: LlmClient,
    item: AdjudicationRequest,
    context: dict[int, dict[str, Any]],
    cross_check: bool,
    stats: CrossCheckStats,
) -> Verdict | None:
    """Ask once, and when cross-checking is on, ask again with the facts swapped.

    A judge shown two statements is influenced by which one it reads first. That is a
    documented property of the technique and not something a better prompt removes, so the
    only way to find out whether a verdict was about the evidence is to present the same
    evidence the other way round and see whether the answer survives.

    The policy when it does not is deliberately asymmetric. A contradiction is the strongest
    thing this system says about a pair of documents, and it says it to a reader who will go
    and look: so it needs both readings to agree. Where one ordering contradicts and the
    other reconciles, the reconciliation is kept and the disagreement is recorded on the
    relation. That is the conservative direction: the pair stays visible, flagged as
    unsettled, rather than being asserted as a conflict on the strength of a coin that
    landed differently the second time.

    One case the comparison cannot see: `refines` is directional, and neither the model's
    schema nor the relation row records which fact is the specific one, so a flip between
    "A refines B" and "B refines A" reads as agreement. Period containment, nearly every
    real instance: is settled by rule long before it reaches here.
    """
    forward = await _ask(client, item, context, swapped=False)
    if not cross_check:
        return forward

    reverse = await _ask(client, item, context, swapped=True)
    stats.checked += 1

    if forward is None or reverse is None:
        # One ordering produced a verdict and the other declined the pair entirely. There is
        # no version of that worth recording as a relationship.
        if forward is not None or reverse is not None:
            stats.dropped += 1
            return None
        return None

    if forward.relation_type == reverse.relation_type:
        stats.agreed += 1
        forward.confidence = round((forward.confidence + reverse.confidence) / 2, 3)
        return forward

    stats.order_sensitive += 1
    kept, other = forward, reverse
    if forward.relation_type == REL_CONTRADICTS:
        kept, other = reverse, forward

    kept.order_sensitive = True
    kept.reverse_relation_type = other.relation_type
    kept.confidence = round(min(kept.confidence, ORDER_SENSITIVE_CONFIDENCE), 3)
    kept.severity = round(kept.severity * ORDER_SENSITIVE_CONFIDENCE, 3)
    kept.explanation = (
        f"{kept.explanation} Presented in the opposite order the same comparison was read "
        f"as {other.relation_type.replace('_', ' ')} instead, so this verdict depends on "
        "which fact came first and is recorded as unsettled."
    ).strip()
    return kept


async def _ask(
    client: LlmClient,
    item: AdjudicationRequest,
    context: dict[int, dict[str, Any]],
    *,
    swapped: bool,
) -> Verdict | None:
    first, second = (item.right, item.left) if swapped else (item.left, item.right)
    return await _adjudicate_one(client, item, context, first, second)


def load_adjudication_context(
    session: Session, requests: list[AdjudicationRequest]
) -> dict[int, dict[str, Any]]:
    """Page text and document identity for every fact involved, fetched in two queries."""
    fact_ids = {item.left.id for item in requests} | {item.right.id for item in requests}
    facts = {item.left.id: item.left for item in requests}
    facts.update({item.right.id: item.right for item in requests})

    page_ids = {fact.page_id for fact in facts.values() if fact.id in fact_ids}
    document_ids = {fact.document_id for fact in facts.values() if fact.id in fact_ids}

    pages = {page.id: page for page in session.query(Page).filter(Page.id.in_(page_ids)).all()}
    documents = {
        document.id: document
        for document in session.query(Document).filter(Document.id.in_(document_ids)).all()
    }

    context: dict[int, dict[str, Any]] = {}
    for fact_id, fact in facts.items():
        page = pages.get(fact.page_id)
        document = documents.get(fact.document_id)
        surrounding = ""
        if page is not None and fact.evidence_start is not None:
            surrounding = expand_to_context(
                page.text,
                fact.evidence_start,
                fact.evidence_end or fact.evidence_start,
                radius=CONTEXT_RADIUS,
            )
        source = "unknown document"
        if document is not None:
            publisher = document.publisher or "unknown publisher"
            title = document.title or document.filename
            page_label = page.printed_label if page else None
            location = f"page {page.page_number}" if page else ""
            if page_label:
                location += f" (printed {page_label})"
            source = f"{title}: {publisher}, {location}"
            if document.as_of_date:
                source += f", data as of {document.as_of_date}"
        context[fact_id] = {"context": truncate(surrounding, 1200), "source": source}
    return context


async def _adjudicate_one(
    client: LlmClient,
    item: AdjudicationRequest,
    context: dict[int, dict[str, Any]],
    first: Fact,
    second: Fact,
) -> Verdict | None:
    request = LlmRequest(
        purpose=PURPOSE_ADJUDICATE,
        system=ADJUDICATE_SYSTEM,
        user=adjudicate_prompt(
            _render(first, context.get(first.id, {})),
            _render(second, context.get(second.id, {})),
            item.note,
        ),
        schema=ADJUDICATION_SCHEMA,
        max_output_tokens=1024,
    )
    try:
        response = await client.complete(request)
    except LlmError as error:
        logger.warning(
            "adjudication failed for facts %s and %s: %s", item.left.id, item.right.id, error
        )
        return None

    return _to_verdict(response.data, item)


def _render(fact: Fact, extra: dict[str, Any]) -> dict[str, Any]:
    normalised = ""
    if fact.value_base is not None and fact.unit_canonical:
        normalised = f"{fact.value_base:,.4g} in base units of {fact.unit_canonical}"
    period = fact.period_label or ""
    if fact.period_start and fact.period_end:
        period = f"{period} ({fact.period_start} to {fact.period_end})".strip()
    return {
        "statement": fact.statement,
        "subject": fact.subject_surface,
        "predicate": fact.predicate_surface,
        "value_text": fact.value_text,
        "normalised": normalised,
        "period": period,
        "qualifiers": fact.qualifiers or {},
        "basis": fact.basis or "not stated",
        "evidence_quote": fact.evidence_quote,
        "source": extra.get("source", ""),
        "context": extra.get("context", ""),
    }


def _to_verdict(data: Any, item: AdjudicationRequest) -> Verdict | None:
    if not isinstance(data, dict):
        return None

    raw_verdict = str(data.get("verdict", "")).strip().lower()
    if raw_verdict not in VERDICTS:
        logger.warning("adjudicator returned an unknown verdict %r", raw_verdict)
        return None
    if raw_verdict == "unrelated":
        # The candidate stage over-proposed. Recording nothing is the correct outcome.
        return None

    dimension = str(data.get("dimension", "")).strip().lower()
    if dimension not in DIMENSIONS:
        dimension = DIM_NONE

    explanation = str(data.get("explanation", "")).strip()
    reconciliation = str(data.get("reconciliation", "")).strip()
    if reconciliation and reconciliation.lower() not in explanation.lower():
        explanation = f"{explanation} {reconciliation}".strip()

    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.6))))
    except (TypeError, ValueError):
        confidence = 0.6

    relation_type = _VERDICT_MAP[raw_verdict]
    severity = 0.0
    if relation_type == REL_CONTRADICTS:
        severity = severity_for(item.left, item.right, item.delta_relative or 0.0) * confidence

    return Verdict(
        relation_type=relation_type,
        dimension=dimension,
        subtype="model_adjudicated",
        explanation=explanation,
        confidence=confidence,
        rule_id=None,
        severity=round(severity, 3),
        delta_absolute=item.delta_absolute,
        delta_relative=item.delta_relative,
        decided_by=DECIDED_BY_MODEL,
    )
