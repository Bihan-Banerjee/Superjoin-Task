"""The four cases the assignment asks to be demonstrated.

Derived by querying the knowledge layer, never pinned to particular facts. Ingest a
different corpus and this endpoint answers from that corpus instead, which is the only
version of the feature that is worth anything: a hardcoded list of examples would prove
the documents contained them, not that the system found them.

Each case has a ranking that expresses what makes a *good* example of it, and the reasons
are returned alongside so the interface can say why this one was chosen.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.serializers import fact_summary, rejection_summary, relation_summary
from app.core.text import similarity
from app.db.engine import db_session
from app.db.models import (
    DECIDED_BY_MODEL,
    DIM_NONE,
    REL_CONTRADICTS,
    REL_CORROBORATES,
    REL_RECONCILED,
    REL_REFINES,
    REL_SUPERSEDES,
    Document,
    Fact,
    Page,
    Rejection,
    Relation,
)

router = APIRouter(prefix="/api/cases", tags=["cases"])

CANDIDATE_POOL = 300
EXAMPLES_PER_CASE = 3


@router.get("")
def get_cases(session: Session = Depends(db_session)) -> dict[str, Any]:
    context = _Context(session)
    return {
        "cases": [
            _corroboration_case(context),
            _contradiction_case(context),
            _reconciled_case(context),
            _failure_case(context),
        ]
    }


class _Context:
    """Loads the pool once and shares it across the four case builders."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.documents = {document.id: document for document in session.scalars(select(Document))}
        self._pages: dict[int, Page] = {}

    def pages_for(self, facts: list[Fact]) -> dict[int, Page]:
        missing = {fact.page_id for fact in facts} - set(self._pages)
        if missing:
            for page in self.session.scalars(select(Page).where(Page.id.in_(missing))):
                self._pages[page.id] = page
        return self._pages

    def relations(self, relation_type: str, limit: int = CANDIDATE_POOL) -> list[Relation]:
        return list(
            self.session.scalars(
                select(Relation)
                .where(Relation.relation_type == relation_type)
                .order_by(Relation.cross_document.desc(), Relation.confidence.desc())
                .limit(limit)
            )
        )

    def render(self, relation: Relation) -> dict[str, Any] | None:
        left = self.session.get(Fact, relation.left_fact_id)
        right = self.session.get(Fact, relation.right_fact_id)
        if left is None or right is None:
            return None
        pages = self.pages_for([left, right])
        return relation_summary(
            relation,
            fact_summary(
                left, document=self.documents.get(left.document_id), page=pages.get(left.page_id)
            ),
            fact_summary(
                right,
                document=self.documents.get(right.document_id),
                page=pages.get(right.page_id),
            ),
        )

    def facts(self, relation: Relation) -> tuple[Fact | None, Fact | None]:
        return (
            self.session.get(Fact, relation.left_fact_id),
            self.session.get(Fact, relation.right_fact_id),
        )


def _case(key: str, title: str, description: str, examples: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "description": description,
        "found": bool(examples),
        "examples": examples,
    }


def _corroboration_case(context: _Context) -> dict[str, Any]:
    """A fact confirmed across documents, ideally stated in different words.

    Ranked so that agreement expressed *differently* outranks agreement expressed the same
    way. Two documents that print the identical sentence prove very little; two that state
    the same amount in different units and different phrasing prove the normalisation works.
    """
    scored: list[tuple[float, dict[str, Any], list[str]]] = []
    for relation in context.relations(REL_CORROBORATES):
        left, right = context.facts(relation)
        if left is None or right is None:
            continue

        reasons: list[str] = []
        score = relation.confidence
        if relation.cross_document:
            score += 1.5
            reasons.append("the two statements come from different documents")
        if left.unit_scale and right.unit_scale and left.unit_scale != right.unit_scale:
            score += 1.2
            reasons.append(
                f"they are written at different scales, {left.unit_surface or left.unit_canonical} "
                f"against {right.unit_surface or right.unit_canonical}"
            )

        wording = similarity(left.evidence_quote, right.evidence_quote)
        if wording < 70:
            score += 0.8
            reasons.append("the wording of the two sources differs substantially")
        if left.predicate_surface != right.predicate_surface:
            score += 0.5
            reasons.append(
                f"the measure is named differently: {left.predicate_surface!r} and "
                f"{right.predicate_surface!r}"
            )

        rendered = context.render(relation)
        if rendered is not None:
            scored.append((score, rendered, reasons))

    scored.sort(key=lambda item: -item[0])
    return _case(
        "corroboration",
        "A fact corroborated across documents",
        "The same claim appears in more than one source. Because comparison happens on the "
        "normalised value rather than the text, agreement is still detected when the "
        "documents use different units, different scales and different wording.",
        [_with_reasons(item) for item in scored[:EXAMPLES_PER_CASE]],
    )


def _contradiction_case(context: _Context) -> dict[str, Any]:
    """A genuine disagreement: same measure, entity and period, no explanation available."""
    scored: list[tuple[float, dict[str, Any], list[str]]] = []
    for relation in context.relations(REL_CONTRADICTS):
        left, right = context.facts(relation)
        if left is None or right is None:
            continue

        reasons: list[str] = []
        score = relation.severity + relation.confidence
        if relation.cross_document:
            score += 1.5
            reasons.append("the conflicting figures come from different documents")
        if left.period_start and left.period_start == right.period_start:
            reasons.append(f"both cover the same period, {left.period_label or left.period_start}")
        if left.evidence_exact and right.evidence_exact:
            score += 0.4
            reasons.append("both quotes were matched exactly in their source pages")
        if relation.delta_relative:
            reasons.append(f"the values differ by {relation.delta_relative:.1%}")
        if relation.decided_by == DECIDED_BY_MODEL:
            reasons.append("the rules could not explain the gap, so it was reviewed in context")

        rendered = context.render(relation)
        if rendered is not None:
            scored.append((score, rendered, reasons))

    scored.sort(key=lambda item: -item[0])
    return _case(
        "contradiction",
        "A genuine or likely contradiction",
        "Two sources state different values for the same measure, entity and period, and no "
        "difference in units, scope, basis or data vintage accounts for it. These are ranked "
        "by how large the disagreement is and how well grounded both sides are.",
        [_with_reasons(item) for item in scored[:EXAMPLES_PER_CASE]],
    )


def _reconciled_case(context: _Context) -> dict[str, Any]:
    """An apparent contradiction that context explains.

    Ranked to favour a large numeric gap with a named explanation, because that is the case
    where a system without this reasoning would have reported a false contradiction: which
    is exactly what the reconciliation logic exists to prevent.
    """
    scored: list[tuple[float, dict[str, Any], list[str]]] = []
    pool = (
        context.relations(REL_RECONCILED)
        + context.relations(REL_REFINES, limit=100)
        + context.relations(REL_SUPERSEDES, limit=100)
    )

    for relation in pool:
        if relation.dimension == DIM_NONE:
            continue
        left, right = context.facts(relation)
        if left is None or right is None:
            continue

        gap = relation.delta_relative or 0.0
        score = relation.confidence + min(gap, 1.0) * 2.0
        reasons = [f"the difference is explained by {relation.dimension.replace('_', ' ')}"]
        if relation.relation_type == REL_SUPERSEDES:
            # The most useful answer of the three: it explains the difference and then says
            # which of the two figures a reader should now be using.
            score += 0.5
            reasons.append("one statement replaces the other rather than competing with it")
        if gap:
            reasons.append(
                f"without that explanation the {gap:.1%} gap would read as a contradiction"
            )
        if relation.cross_document:
            score += 1.0
            reasons.append("the two statements come from different documents")
        if relation.decided_by == DECIDED_BY_MODEL:
            reasons.append("decided by reading the surrounding evidence, not by rule")
        else:
            reasons.append(f"decided mechanically by the {relation.rule_id} rule")

        rendered = context.render(relation)
        if rendered is not None:
            scored.append((score, rendered, reasons))

    scored.sort(key=lambda item: -item[0])
    return _case(
        "reconciled",
        "An apparent contradiction explained by context",
        "The values differ, but a difference in period, unit scale, currency, scope, segment "
        "or data vintage accounts for it. Each example names the dimension responsible, so "
        "the reconciliation can be checked rather than taken on trust.",
        [_with_reasons(item) for item in scored[:EXAMPLES_PER_CASE]],
    )


def _failure_case(context: _Context) -> dict[str, Any]:
    """What the pipeline got wrong, measured rather than described.

    Every rejection is a candidate fact that was proposed and then refused, with the reason
    it was refused. Reporting the distribution is a more honest answer to "what does not
    work" than a paragraph of prose, and it is produced by the same run that produced the
    facts.
    """
    session = context.session
    by_reason = session.execute(
        select(Rejection.reason, func.count(Rejection.id))
        .group_by(Rejection.reason)
        .order_by(func.count(Rejection.id).desc())
    ).all()

    proposed = int(session.scalar(select(func.count(Fact.id))) or 0)
    rejected = int(session.scalar(select(func.count(Rejection.id))) or 0)

    samples = []
    for reason, _count in by_reason[:4]:
        rejection = session.scalar(
            select(Rejection)
            .where(Rejection.reason == reason)
            .order_by(Rejection.id.desc())
            .limit(1)
        )
        if rejection is not None:
            samples.append(
                rejection_summary(rejection, context.documents.get(rejection.document_id))
            )

    total = proposed + rejected
    return {
        "key": "failure",
        "title": "An extraction or reasoning failure",
        "description": (
            "Candidate facts the pipeline proposed and then refused, with the reason. These "
            "are recorded rather than logged, so the system's own reliability is a number it "
            "reports instead of a claim it makes."
        ),
        "found": rejected > 0,
        "summary": {
            "facts_kept": proposed,
            "candidates_rejected": rejected,
            "grounding_pass_rate": round(proposed / total, 3) if total else 0.0,
        },
        "by_reason": [
            {"reason": reason, "count": int(count), "explanation": _REASON_TEXT.get(reason, "")}
            for reason, count in by_reason
        ],
        "examples": samples,
    }


_REASON_TEXT = {
    "quote_not_found": (
        "The evidence quoted for the fact could not be located on the page it was said to "
        "come from. The fact is discarded rather than kept with a weaker citation."
    ),
    "value_absent_from_quote": (
        "The quote was real but did not contain the value attributed to it. This is the "
        "failure mode that matters most, because the result looks correct in a table."
    ),
    "unattributed_by_model": (
        "The extractor saw a value it could not attach to a subject and measure and said so "
        "instead of guessing. Most of these are chart labels whose series is ambiguous."
    ),
    "subject_unresolved": (
        "The subject was a pronoun or a bare 'the Company', which cannot be compared across "
        "documents where more than one company appears."
    ),
    "duplicate_of_existing_fact": (
        "The same fact was extracted twice from one page, usually because it appears in both "
        "a table and the prose describing it."
    ),
    "quote_too_short": (
        "The evidence was too short to identify what the value measures, so the fact could "
        "not be verified even though the number was present."
    ),
    "low_confidence": "The extractor reported low confidence in its own attribution.",
    "extraction_call_failed": (
        "The model call for this page did not complete. The page is recorded as failed rather "
        "than treated as containing no facts."
    ),
    "missing_required_fields": "The extractor returned an incomplete fact.",
    "predicate_unresolved": "The measure was too vague to compare against anything.",
}


def _with_reasons(item: tuple[float, dict[str, Any], list[str]]) -> dict[str, Any]:
    _score, relation, reasons = item
    payload = dict(relation)
    payload["selected_because"] = reasons
    return payload
