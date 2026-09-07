"""Fact browsing, filtering and search."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Select, func, or_, select, text
from sqlalchemy.orm import Session, selectinload

from app.api.serializers import fact_detail, fact_summary, relation_summary
from app.db.engine import db_session
from app.db.models import Document, Fact, Page, Relation

router = APIRouter(prefix="/api/facts", tags=["facts"])

SORT_COLUMNS = {
    "confidence": Fact.confidence,
    "value": Fact.value_base,
    "period": Fact.period_start,
    "page": Fact.page_id,
    "id": Fact.id,
}


@router.get("")
def list_facts(
    session: Session = Depends(db_session),
    document_id: int | None = None,
    measure_id: int | None = None,
    entity_id: int | None = None,
    kind: str | None = None,
    unit_class: str | None = None,
    currency: str | None = None,
    basis: str | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
    has_relations: bool | None = None,
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    q: str | None = None,
    sort: str = Query(default="id"),
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    statement = select(Fact).options(selectinload(Fact.measure), selectinload(Fact.entity))
    statement = _apply_filters(
        statement,
        session,
        document_id=document_id,
        measure_id=measure_id,
        entity_id=entity_id,
        kind=kind,
        unit_class=unit_class,
        currency=currency,
        basis=basis,
        period_start=period_start,
        period_end=period_end,
        has_relations=has_relations,
        min_confidence=min_confidence,
        q=q,
    )

    total = session.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))

    column = SORT_COLUMNS.get(sort, Fact.id)
    statement = statement.order_by(column.desc() if order == "desc" else column.asc())
    facts = list(session.scalars(statement.limit(limit).offset(offset)))

    documents, pages = _load_sources(session, facts)
    return {
        "total": int(total or 0),
        "limit": limit,
        "offset": offset,
        "facts": [
            fact_summary(
                fact, document=documents.get(fact.document_id), page=pages.get(fact.page_id)
            )
            for fact in facts
        ],
    }


def _apply_filters(
    statement: Select,
    session: Session,
    *,
    document_id: int | None,
    measure_id: int | None,
    entity_id: int | None,
    kind: str | None,
    unit_class: str | None,
    currency: str | None,
    basis: str | None,
    period_start: str | None,
    period_end: str | None,
    has_relations: bool | None,
    min_confidence: float,
    q: str | None,
) -> Select:
    if document_id is not None:
        statement = statement.where(Fact.document_id == document_id)
    if measure_id is not None:
        statement = statement.where(Fact.measure_id == measure_id)
    if entity_id is not None:
        statement = statement.where(Fact.entity_id == entity_id)
    if kind:
        statement = statement.where(Fact.kind == kind)
    if unit_class:
        statement = statement.where(Fact.unit_class == unit_class)
    if currency:
        statement = statement.where(Fact.currency == currency)
    if basis:
        statement = statement.where(Fact.basis == basis)
    # Overlap rather than containment: a reviewer filtering to a year wants everything that
    # touches it, including the quarters inside it.
    if period_start:
        statement = statement.where(Fact.period_end >= period_start)
    if period_end:
        statement = statement.where(Fact.period_start <= period_end)
    if min_confidence > 0:
        statement = statement.where(Fact.confidence >= min_confidence)

    if has_relations is not None:
        related = select(Relation.left_fact_id).union(select(Relation.right_fact_id))
        condition = Fact.id.in_(related)
        statement = statement.where(condition if has_relations else ~condition)

    if q and q.strip():
        matched = _search_ids(session, q.strip())
        if matched is None:
            pattern = f"%{q.strip()}%"
            statement = statement.where(
                or_(
                    Fact.statement.ilike(pattern),
                    Fact.subject_surface.ilike(pattern),
                    Fact.predicate_surface.ilike(pattern),
                    Fact.evidence_quote.ilike(pattern),
                )
            )
        else:
            statement = statement.where(Fact.id.in_(matched))
    return statement


def _search_ids(session: Session, query: str) -> list[int] | None:
    """Full-text search, falling back to LIKE when the query is not valid FTS syntax.

    Reviewers type quotation marks and hyphens into search boxes, and FTS5 treats several
    of those as operators. Rather than reject the query, the caller retries with LIKE.
    """
    sanitised = " ".join(
        f'"{token}"'
        for token in "".join(
            character if character.isalnum() else " " for character in query
        ).split()
        if token
    )
    if not sanitised:
        return None
    try:
        rows = session.execute(
            text("SELECT rowid FROM facts_fts WHERE facts_fts MATCH :query LIMIT 2000"),
            {"query": sanitised},
        ).all()
    except Exception:
        return None
    return [int(row[0]) for row in rows]


@router.get("/{fact_id}")
def get_fact(fact_id: int, session: Session = Depends(db_session)) -> dict[str, Any]:
    fact = session.get(Fact, fact_id)
    if fact is None:
        raise HTTPException(status_code=404, detail="fact not found")

    relations = list(
        session.scalars(
            select(Relation).where(
                or_(Relation.left_fact_id == fact_id, Relation.right_fact_id == fact_id)
            )
        )
    )
    counterpart_ids = {
        relation.right_fact_id if relation.left_fact_id == fact_id else relation.left_fact_id
        for relation in relations
    }
    counterparts = {
        other.id: other
        for other in session.scalars(select(Fact).where(Fact.id.in_(counterpart_ids)))
    }

    all_facts = [fact, *counterparts.values()]
    documents, pages = _load_sources(session, all_facts)

    rendered = []
    for relation in relations:
        other_id = (
            relation.right_fact_id if relation.left_fact_id == fact_id else relation.left_fact_id
        )
        other = counterparts.get(other_id)
        if other is None:
            continue
        rendered.append(
            relation_summary(
                relation,
                fact_summary(
                    fact, document=documents.get(fact.document_id), page=pages.get(fact.page_id)
                ),
                fact_summary(
                    other, document=documents.get(other.document_id), page=pages.get(other.page_id)
                ),
            )
        )

    rendered.sort(key=lambda item: (-item["severity"], -item["confidence"]))
    return fact_detail(fact, documents.get(fact.document_id), pages.get(fact.page_id), rendered)


def _load_sources(
    session: Session, facts: list[Fact]
) -> tuple[dict[int, Document], dict[int, Page]]:
    if not facts:
        return {}, {}
    document_ids = {fact.document_id for fact in facts}
    page_ids = {fact.page_id for fact in facts}
    documents = {
        document.id: document
        for document in session.scalars(select(Document).where(Document.id.in_(document_ids)))
    }
    pages = {page.id: page for page in session.scalars(select(Page).where(Page.id.in_(page_ids)))}
    return documents, pages
