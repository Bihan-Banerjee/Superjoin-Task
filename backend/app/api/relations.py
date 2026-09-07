"""Relation browsing, and the registry that shows the schema growing."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.serializers import fact_summary, measure_summary, relation_summary
from app.db.engine import db_session
from app.db.models import Document, Entity, Fact, Measure, Page, QualifierKey, Relation

router = APIRouter(prefix="/api", tags=["relations"])


@router.get("/relations")
def list_relations(
    session: Session = Depends(db_session),
    relation_type: str | None = None,
    dimension: str | None = None,
    decided_by: str | None = None,
    document_id: int | None = None,
    measure_id: int | None = None,
    cross_document: bool | None = None,
    min_severity: float = Query(default=0.0, ge=0.0, le=1.0),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    sort: str = Query(default="severity", pattern="^(severity|confidence|id)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    statement = select(Relation)

    if relation_type:
        statement = statement.where(Relation.relation_type == relation_type)
    if dimension:
        statement = statement.where(Relation.dimension == dimension)
    if decided_by:
        statement = statement.where(Relation.decided_by == decided_by)
    if cross_document is not None:
        statement = statement.where(Relation.cross_document == cross_document)
    if min_severity > 0:
        statement = statement.where(Relation.severity >= min_severity)
    if min_confidence > 0:
        statement = statement.where(Relation.confidence >= min_confidence)

    if document_id is not None or measure_id is not None:
        column = Fact.document_id if document_id is not None else Fact.measure_id
        value = document_id if document_id is not None else measure_id
        matching = select(Fact.id).where(column == value)
        statement = statement.where(
            or_(Relation.left_fact_id.in_(matching), Relation.right_fact_id.in_(matching))
        )

    total = session.scalar(select(func.count()).select_from(statement.subquery()))

    if sort == "severity":
        statement = statement.order_by(Relation.severity.desc(), Relation.confidence.desc())
    elif sort == "confidence":
        statement = statement.order_by(Relation.confidence.desc())
    else:
        statement = statement.order_by(Relation.id.asc())

    relations = list(session.scalars(statement.limit(limit).offset(offset)))
    return {
        "total": int(total or 0),
        "limit": limit,
        "offset": offset,
        "relations": _render(session, relations),
        "counts": _type_counts(session),
    }


@router.get("/relations/{relation_id}")
def get_relation(relation_id: int, session: Session = Depends(db_session)) -> dict[str, Any]:
    relation = session.get(Relation, relation_id)
    if relation is None:
        raise HTTPException(status_code=404, detail="relation not found")
    rendered = _render(session, [relation])
    return rendered[0]


def _render(session: Session, relations: list[Relation]) -> list[dict[str, Any]]:
    if not relations:
        return []

    fact_ids = {relation.left_fact_id for relation in relations} | {
        relation.right_fact_id for relation in relations
    }
    facts = {
        fact.id: fact
        for fact in session.scalars(
            select(Fact)
            .options(selectinload(Fact.measure), selectinload(Fact.entity))
            .where(Fact.id.in_(fact_ids))
        )
    }
    documents = {
        document.id: document
        for document in session.scalars(
            select(Document).where(Document.id.in_({fact.document_id for fact in facts.values()}))
        )
    }
    pages = {
        page.id: page
        for page in session.scalars(
            select(Page).where(Page.id.in_({fact.page_id for fact in facts.values()}))
        )
    }

    rendered: list[dict[str, Any]] = []
    for relation in relations:
        left = facts.get(relation.left_fact_id)
        right = facts.get(relation.right_fact_id)
        if left is None or right is None:
            continue
        rendered.append(
            relation_summary(
                relation,
                fact_summary(
                    left, document=documents.get(left.document_id), page=pages.get(left.page_id)
                ),
                fact_summary(
                    right, document=documents.get(right.document_id), page=pages.get(right.page_id)
                ),
            )
        )
    return rendered


def _type_counts(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(Relation.relation_type, func.count(Relation.id)).group_by(Relation.relation_type)
    ).all()
    return {str(relation_type): int(count) for relation_type, count in rows}


@router.get("/measures")
def list_measures(
    session: Session = Depends(db_session),
    q: str | None = None,
    unit_class: str | None = None,
    min_documents: int = Query(default=0, ge=0),
    sort: str = Query(default="facts", pattern="^(facts|documents|name|recent)$"),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    """The measure registry.

    This is the schema. It is data rather than code, so a document introducing a kind of
    measurement the system has never seen adds a row here instead of needing a migration.
    """
    statement = select(Measure)
    if q:
        statement = statement.where(Measure.name.ilike(f"%{q.strip()}%"))
    if unit_class:
        statement = statement.where(Measure.unit_class == unit_class)
    if min_documents:
        statement = statement.where(Measure.document_count >= min_documents)

    if sort == "facts":
        statement = statement.order_by(Measure.fact_count.desc(), Measure.name.asc())
    elif sort == "documents":
        statement = statement.order_by(Measure.document_count.desc(), Measure.fact_count.desc())
    elif sort == "recent":
        statement = statement.order_by(Measure.id.desc())
    else:
        statement = statement.order_by(Measure.name.asc())

    measures = list(session.scalars(statement.limit(limit)))
    titles = _document_titles(session, [m.first_seen_document_id for m in measures])
    return {
        "total": int(session.scalar(select(func.count(Measure.id))) or 0),
        "measures": [
            measure_summary(measure, titles.get(measure.first_seen_document_id))
            for measure in measures
        ],
        "unit_classes": [
            {"unit_class": row[0], "count": int(row[1])}
            for row in session.execute(
                select(Measure.unit_class, func.count(Measure.id))
                .group_by(Measure.unit_class)
                .order_by(func.count(Measure.id).desc())
            ).all()
        ],
    }


@router.get("/entities")
def list_entities(
    session: Session = Depends(db_session),
    q: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    statement = select(Entity).order_by(Entity.fact_count.desc(), Entity.name.asc())
    if q:
        statement = statement.where(Entity.name.ilike(f"%{q.strip()}%"))
    entities = list(session.scalars(statement.limit(limit)))
    titles = _document_titles(session, [entity.first_seen_document_id for entity in entities])
    return {
        "total": int(session.scalar(select(func.count(Entity.id))) or 0),
        "entities": [
            {
                "id": entity.id,
                "slug": entity.slug,
                "name": entity.name,
                "entity_type": entity.entity_type,
                "aliases": list(entity.aliases or []),
                "fact_count": entity.fact_count,
                "first_seen_document": titles.get(entity.first_seen_document_id),
            }
            for entity in entities
        ],
    }


@router.get("/qualifiers")
def list_qualifier_keys(session: Session = Depends(db_session)) -> dict[str, Any]:
    """Qualifier dimensions the corpus has introduced.

    These are the axes along which two facts can differ without disagreeing, so the set of
    them is a direct readout of how much context the layer has learned to distinguish.
    """
    keys = list(session.scalars(select(QualifierKey).order_by(QualifierKey.fact_count.desc())))
    titles = _document_titles(session, [key.first_seen_document_id for key in keys])
    return {
        "qualifiers": [
            {
                "key": key.key,
                "values": list(key.values or []),
                "value_count": len(key.values or []),
                "fact_count": key.fact_count,
                "discriminating": key.discriminating,
                "first_seen_document": titles.get(key.first_seen_document_id),
            }
            for key in keys
        ]
    }


def _document_titles(session: Session, ids: list[int | None]) -> dict[int, str]:
    wanted = {value for value in ids if value}
    if not wanted:
        return {}
    return {
        document.id: document.title or document.filename
        for document in session.scalars(select(Document).where(Document.id.in_(wanted)))
    }
