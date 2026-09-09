"""Measurements of how well the pipeline is doing.

Everything on this page is computed from what the pipeline actually recorded during ingest.
None of it is a claim the code makes about itself.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.engine import db_session
from app.db.models import (
    DECIDED_BY_MODEL,
    Document,
    Entity,
    Fact,
    LlmCall,
    Measure,
    Page,
    QualifierKey,
    Rejection,
    Relation,
)

router = APIRouter(prefix="/api/evaluation", tags=["evaluation"])


@router.get("")
def evaluation(session: Session = Depends(db_session)) -> dict[str, Any]:
    facts = int(session.scalar(select(func.count(Fact.id))) or 0)
    rejections = int(session.scalar(select(func.count(Rejection.id))) or 0)
    proposed = facts + rejections

    exact = int(
        session.scalar(select(func.count(Fact.id)).where(Fact.evidence_exact.is_(True))) or 0
    )
    located = int(session.scalar(select(func.count(Fact.id)).where(Fact.bboxes != "[]")) or 0)

    return {
        "corpus": _corpus(session),
        "grounding": {
            "candidates_proposed": proposed,
            "facts_kept": facts,
            "candidates_rejected": rejections,
            "pass_rate": round(facts / proposed, 4) if proposed else 0.0,
            "quotes_matched_exactly": exact,
            "exact_match_rate": round(exact / facts, 4) if facts else 0.0,
            "facts_located_on_page": located,
            "page_location_rate": round(located / facts, 4) if facts else 0.0,
        },
        "rejections_by_reason": [
            {"reason": reason, "count": int(count)}
            for reason, count in session.execute(
                select(Rejection.reason, func.count(Rejection.id))
                .group_by(Rejection.reason)
                .order_by(func.count(Rejection.id).desc())
            ).all()
        ],
        "coverage": _coverage(session),
        "relations": _relations(session),
        "registry": _registry(session),
        "normalisation": _normalisation(session),
        "model_usage": _usage(session),
    }


def _corpus(session: Session) -> dict[str, Any]:
    return {
        "documents": int(session.scalar(select(func.count(Document.id))) or 0),
        "pages": int(session.scalar(select(func.count(Page.id))) or 0),
        "pages_extracted": int(
            session.scalar(select(func.count(Page.id)).where(Page.extracted.is_(True))) or 0
        ),
        "page_types": [
            {"page_type": page_type, "count": int(count)}
            for page_type, count in session.execute(
                select(Page.page_type, func.count(Page.id))
                .group_by(Page.page_type)
                .order_by(func.count(Page.id).desc())
            ).all()
        ],
    }


def _coverage(session: Session) -> list[dict[str, Any]]:
    """Per-document yield, so a document the pipeline handled badly is visible."""
    rows = session.execute(
        select(
            Document.id,
            Document.title,
            Document.filename,
            Document.page_count,
            func.count(Fact.id),
        )
        .outerjoin(Fact, Fact.document_id == Document.id)
        .group_by(Document.id)
        .order_by(Document.id)
    ).all()

    rejections = dict(
        session.execute(
            select(Rejection.document_id, func.count(Rejection.id)).group_by(Rejection.document_id)
        ).all()
    )
    extracted = dict(
        session.execute(
            select(Page.document_id, func.count(Page.id))
            .where(Page.extracted.is_(True))
            .group_by(Page.document_id)
        ).all()
    )

    coverage = []
    for document_id, title, filename, page_count, fact_count in rows:
        pages = int(extracted.get(document_id, 0))
        coverage.append(
            {
                "document_id": document_id,
                "title": title or filename,
                "page_count": page_count,
                "pages_extracted": pages,
                "facts": int(fact_count),
                "rejections": int(rejections.get(document_id, 0)),
                "facts_per_extracted_page": round(int(fact_count) / pages, 2) if pages else 0.0,
            }
        )
    return coverage


def _relations(session: Session) -> dict[str, Any]:
    total = int(session.scalar(select(func.count(Relation.id))) or 0)
    return {
        "total": total,
        "cross_document": int(
            session.scalar(select(func.count(Relation.id)).where(Relation.cross_document.is_(True)))
            or 0
        ),
        "by_type": [
            {"type": relation_type, "count": int(count)}
            for relation_type, count in session.execute(
                select(Relation.relation_type, func.count(Relation.id))
                .group_by(Relation.relation_type)
                .order_by(func.count(Relation.id).desc())
            ).all()
        ],
        "by_dimension": [
            {"dimension": dimension, "count": int(count)}
            for dimension, count in session.execute(
                select(Relation.dimension, func.count(Relation.id))
                .group_by(Relation.dimension)
                .order_by(func.count(Relation.id).desc())
            ).all()
        ],
        "by_decision": [
            {"decided_by": decided_by, "count": int(count)}
            for decided_by, count in session.execute(
                select(Relation.decided_by, func.count(Relation.id)).group_by(Relation.decided_by)
            ).all()
        ],
        "by_rule": [
            {"rule_id": rule_id, "count": int(count)}
            for rule_id, count in session.execute(
                select(Relation.rule_id, func.count(Relation.id))
                .where(Relation.rule_id.isnot(None))
                .group_by(Relation.rule_id)
                .order_by(func.count(Relation.id).desc())
            ).all()
        ],
        "adjudication": _adjudication(session),
    }


def _adjudication(session: Session) -> dict[str, Any]:
    """How steady the model was on the pairs the rules could not settle.

    Each escalated pair is read twice with the two facts swapped. Pairs where the answer
    changed are kept, marked, and counted here: the number is a direct measurement of how
    much of the model's judgement was about the evidence and how much was about the order it
    happened to be presented in.
    """
    decided_by_model = int(
        session.scalar(
            select(func.count(Relation.id)).where(Relation.decided_by == DECIDED_BY_MODEL)
        )
        or 0
    )
    unsettled = int(
        session.scalar(
            select(func.count(Relation.id)).where(
                Relation.raw["order_sensitive"].as_boolean().is_(True)
            )
        )
        or 0
    )
    return {
        "decided_by_model": decided_by_model,
        "order_sensitive": unsettled,
        "order_sensitive_rate": (
            round(unsettled / decided_by_model, 3) if decided_by_model else 0.0
        ),
    }


def _registry(session: Session) -> dict[str, Any]:
    measures = int(session.scalar(select(func.count(Measure.id))) or 0)
    shared = int(
        session.scalar(select(func.count(Measure.id)).where(Measure.document_count > 1)) or 0
    )
    return {
        "measures": measures,
        "measures_in_multiple_documents": shared,
        "shared_measure_rate": round(shared / measures, 4) if measures else 0.0,
        "entities": int(session.scalar(select(func.count(Entity.id))) or 0),
        "qualifier_keys": int(session.scalar(select(func.count(QualifierKey.id))) or 0),
    }


def _normalisation(session: Session) -> dict[str, Any]:
    """How much of the corpus reached a comparable form.

    A fact with no resolved unit or no resolved period can still be read, but it cannot
    participate in most comparisons, so this is the ceiling on what linking can find.
    """
    facts = int(session.scalar(select(func.count(Fact.id))) or 0)
    numeric = int(
        session.scalar(select(func.count(Fact.id)).where(Fact.value_number.isnot(None))) or 0
    )
    with_unit = int(
        session.scalar(select(func.count(Fact.id)).where(Fact.unit_class.isnot(None))) or 0
    )
    with_period = int(
        session.scalar(select(func.count(Fact.id)).where(Fact.period_start.isnot(None))) or 0
    )
    with_measure = int(
        session.scalar(select(func.count(Fact.id)).where(Fact.measure_id.isnot(None))) or 0
    )
    return {
        "facts": facts,
        "numeric": numeric,
        "unit_resolved": with_unit,
        "unit_resolution_rate": round(with_unit / numeric, 4) if numeric else 0.0,
        "period_resolved": with_period,
        "period_resolution_rate": round(with_period / facts, 4) if facts else 0.0,
        "measure_assigned": with_measure,
        "measure_assignment_rate": round(with_measure / facts, 4) if facts else 0.0,
        "by_unit_class": [
            {"unit_class": unit_class, "count": int(count)}
            for unit_class, count in session.execute(
                select(Fact.unit_class, func.count(Fact.id))
                .where(Fact.unit_class.isnot(None))
                .group_by(Fact.unit_class)
                .order_by(func.count(Fact.id).desc())
            ).all()
        ],
    }


def _usage(session: Session) -> dict[str, Any]:
    total = int(session.scalar(select(func.count(LlmCall.id))) or 0)
    cached = int(
        session.scalar(select(func.count(LlmCall.id)).where(LlmCall.cached.is_(True))) or 0
    )
    failed = int(session.scalar(select(func.count(LlmCall.id)).where(LlmCall.ok.is_(False))) or 0)
    return {
        "calls": total,
        "billed_calls": total - cached,
        "cache_hits": cached,
        "cache_hit_rate": round(cached / total, 4) if total else 0.0,
        "failed_calls": failed,
        "input_tokens": int(session.scalar(select(func.sum(LlmCall.input_tokens))) or 0),
        "output_tokens": int(session.scalar(select(func.sum(LlmCall.output_tokens))) or 0),
        "median_latency_ms": _median_latency(session),
        "by_purpose": [
            {"purpose": purpose, "count": int(count)}
            for purpose, count in session.execute(
                select(LlmCall.purpose, func.count(LlmCall.id))
                .group_by(LlmCall.purpose)
                .order_by(func.count(LlmCall.id).desc())
            ).all()
        ],
    }


def _median_latency(session: Session) -> int:
    values = [
        int(row[0])
        for row in session.execute(
            select(LlmCall.latency_ms)
            .where(LlmCall.cached.is_(False), LlmCall.latency_ms > 0)
            .order_by(LlmCall.latency_ms)
        ).all()
    ]
    if not values:
        return 0
    return values[len(values) // 2]


@router.get("/rejections")
def list_rejections(
    session: Session = Depends(db_session),
    reason: str | None = None,
    document_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    from app.api.serializers import rejection_summary

    statement = select(Rejection).order_by(Rejection.id.desc())
    if reason:
        statement = statement.where(Rejection.reason == reason)
    if document_id is not None:
        statement = statement.where(Rejection.document_id == document_id)

    total = int(session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
    rows = list(session.scalars(statement.limit(limit).offset(offset)))
    documents = {
        document.id: document
        for document in session.scalars(
            select(Document).where(Document.id.in_({row.document_id for row in rows}))
        )
    }
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "rejections": [rejection_summary(row, documents.get(row.document_id)) for row in rows],
    }
