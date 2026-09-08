"""Bulk export of facts and relations.

The interface is built for reading one claim carefully. This is for the other thing a
reviewer wants to do: take the whole set away and sort it in a spreadsheet, or diff two runs
to see whether a prompt change helped.

CSV is flat on purpose. A fact's qualifiers are a small mapping and its evidence box is a
list of rectangles, neither of which survives a spreadsheet cell usefully, so the CSV carries
the columns worth sorting and filtering on and the JSON carries everything. Both are
streamed, because a corpus of a few thousand facts should not be assembled in memory first.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.engine import db_session
from app.db.models import Document, Fact, Page, Relation

router = APIRouter(prefix="/api/export", tags=["export"])

# Rows fetched per round trip. Large enough that the query cost is amortised, small enough
# that the response starts moving quickly and memory stays flat.
CHUNK = 500

FACT_COLUMNS = [
    "fact_id",
    "document",
    "publisher",
    "page",
    "printed_page",
    "kind",
    "subject",
    "measure",
    "measure_canonical",
    "value_text",
    "value_number",
    "value_base",
    "unit",
    "unit_class",
    "currency",
    "period_label",
    "period_start",
    "period_end",
    "basis",
    "qualifiers",
    "confidence",
    "evidence_score",
    "evidence_exact",
    "evidence_quote",
]

RELATION_COLUMNS = [
    "relation_id",
    "type",
    "subtype",
    "dimension",
    "decided_by",
    "rule_id",
    "confidence",
    "severity",
    "cross_document",
    "order_sensitive",
    "delta_relative",
    "explanation",
    "left_fact_id",
    "left_document",
    "left_value",
    "left_period",
    "right_fact_id",
    "right_document",
    "right_value",
    "right_period",
    "superseded_fact_id",
]


def _stream_csv(columns: list[str], rows: Iterator[dict[str, Any]]) -> Iterator[str]:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    yield buffer.getvalue()
    for row in rows:
        buffer.seek(0)
        buffer.truncate(0)
        writer.writerow(row)
        yield buffer.getvalue()


def _stream_json(rows: Iterator[dict[str, Any]]) -> Iterator[str]:
    yield "[\n"
    first = True
    for row in rows:
        yield ("" if first else ",\n") + json.dumps(row, ensure_ascii=False, default=str)
        first = False
    yield "\n]\n"


def _fact_rows(session: Session, document_id: int | None) -> Iterator[dict[str, Any]]:
    statement = (
        select(Fact)
        .options(selectinload(Fact.measure))
        .order_by(Fact.document_id, Fact.page_id, Fact.id)
        .execution_options(yield_per=CHUNK)
    )
    if document_id is not None:
        statement = statement.where(Fact.document_id == document_id)

    documents = {row.id: row for row in session.scalars(select(Document))}
    pages = {row.id: row for row in session.scalars(select(Page))}

    for fact in session.scalars(statement):
        document = documents.get(fact.document_id)
        page = pages.get(fact.page_id)
        yield {
            "fact_id": fact.id,
            "document": (document.title or document.filename) if document else None,
            "publisher": document.publisher if document else None,
            "page": page.page_number if page else None,
            "printed_page": page.printed_label if page else None,
            "kind": fact.kind,
            "subject": fact.subject_surface,
            "measure": fact.predicate_surface,
            "measure_canonical": fact.measure.name if fact.measure else None,
            "value_text": fact.value_text,
            "value_number": fact.value_number,
            "value_base": fact.value_base,
            "unit": fact.unit_canonical,
            "unit_class": fact.unit_class,
            "currency": fact.currency,
            "period_label": fact.period_label,
            "period_start": fact.period_start,
            "period_end": fact.period_end,
            "basis": fact.basis,
            # Flattened for the spreadsheet; the JSON export keeps the mapping.
            "qualifiers": "; ".join(
                f"{key}={value}" for key, value in sorted((fact.qualifiers or {}).items())
            ),
            "confidence": round(fact.confidence, 3),
            "evidence_score": round(fact.evidence_score, 1),
            "evidence_exact": fact.evidence_exact,
            "evidence_quote": fact.evidence_quote,
        }


def _relation_rows(session: Session, document_id: int | None) -> Iterator[dict[str, Any]]:
    statement = (
        select(Relation)
        .order_by(Relation.severity.desc(), Relation.id)
        .execution_options(yield_per=CHUNK)
    )
    facts = {row.id: row for row in session.scalars(select(Fact))}
    documents = {row.id: row for row in session.scalars(select(Document))}

    def describe(fact_id: int) -> tuple[str | None, str | None, str | None]:
        fact = facts.get(fact_id)
        if fact is None:
            return None, None, None
        document = documents.get(fact.document_id)
        title = (document.title or document.filename) if document else None
        return title, fact.value_text, fact.period_label

    for relation in session.scalars(statement):
        left = facts.get(relation.left_fact_id)
        right = facts.get(relation.right_fact_id)
        if document_id is not None and document_id not in {
            getattr(left, "document_id", None),
            getattr(right, "document_id", None),
        }:
            continue
        left_document, left_value, left_period = describe(relation.left_fact_id)
        right_document, right_value, right_period = describe(relation.right_fact_id)
        raw = relation.raw or {}
        yield {
            "relation_id": relation.id,
            "type": relation.relation_type,
            "subtype": relation.subtype,
            "dimension": relation.dimension,
            "decided_by": relation.decided_by,
            "rule_id": relation.rule_id,
            "confidence": round(relation.confidence, 3),
            "severity": round(relation.severity, 3),
            "cross_document": relation.cross_document,
            "order_sensitive": bool(raw.get("order_sensitive")),
            "delta_relative": relation.delta_relative,
            "explanation": relation.explanation,
            "left_fact_id": relation.left_fact_id,
            "left_document": left_document,
            "left_value": left_value,
            "left_period": left_period,
            "right_fact_id": relation.right_fact_id,
            "right_document": right_document,
            "right_value": right_value,
            "right_period": right_period,
            "superseded_fact_id": raw.get("superseded_fact_id"),
        }


def _respond(
    name: str, fmt: str, columns: list[str], rows: Iterator[dict[str, Any]]
) -> StreamingResponse:
    if fmt == "json":
        return StreamingResponse(
            _stream_json(rows),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{name}.json"'},
        )
    return StreamingResponse(
        _stream_csv(columns, rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
    )


@router.get("/facts")
def export_facts(
    session: Session = Depends(db_session),
    document_id: int | None = None,
    format: str = Query(default="csv", pattern="^(csv|json)$"),
) -> StreamingResponse:
    return _respond("facts", format, FACT_COLUMNS, _fact_rows(session, document_id))


@router.get("/relations")
def export_relations(
    session: Session = Depends(db_session),
    document_id: int | None = None,
    format: str = Query(default="csv", pattern="^(csv|json)$"),
) -> StreamingResponse:
    return _respond("relations", format, RELATION_COLUMNS, _relation_rows(session, document_id))
