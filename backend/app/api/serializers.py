"""Response shapes.

The interface is a review tool, so responses carry what a reviewer needs to judge a fact
without a second request: the evidence, where it sits on the page, and how it was
normalised. Relations carry both facts in full for the same reason — a verdict is not
reviewable without the two things it is a verdict about.
"""

from __future__ import annotations

from typing import Any

from app.db.models import Document, Fact, Job, Measure, Page, Rejection, Relation


def document_summary(document: Document, counts: dict[str, int] | None = None) -> dict[str, Any]:
    counts = counts or {}
    return {
        "id": document.id,
        "filename": document.filename,
        "title": document.title or document.filename,
        "publisher": document.publisher,
        "doc_type": document.doc_type,
        "subject_entity": document.subject_entity,
        "as_of_date": document.as_of_date,
        "published_date": document.published_date,
        "period_label": document.period_label,
        "default_currency": document.default_currency,
        "default_scale": document.default_scale,
        "fiscal_convention": document.fiscal_convention,
        "reporting_basis": document.reporting_basis,
        "page_count": document.page_count,
        "byte_size": document.byte_size,
        "status": document.status,
        "error": document.error,
        "sha256": document.sha256,
        "near_duplicate_of": document.near_duplicate_of,
        "content_overlap": document.content_overlap,
        "created_at": document.created_at.isoformat() if document.created_at else None,
        "fact_count": counts.get("facts", 0),
        "relation_count": counts.get("relations", 0),
        "rejection_count": counts.get("rejections", 0),
    }


def document_detail(
    document: Document, counts: dict[str, int], pages: list[Page]
) -> dict[str, Any]:
    payload = document_summary(document, counts)
    payload["profile"] = document.profile or {}
    payload["pages"] = [
        {
            "page_number": page.page_number,
            "printed_label": page.printed_label,
            "page_type": page.page_type,
            "word_count": page.word_count,
            "extracted": page.extracted,
            "unit_currency": page.unit_currency,
            "unit_scale": page.unit_scale,
            "reason": (page.layout or {}).get("reason"),
        }
        for page in pages
    ]
    return payload


def fact_summary(
    fact: Fact, *, document: Document | None = None, page: Page | None = None
) -> dict[str, Any]:
    return {
        "id": fact.id,
        "kind": fact.kind,
        "statement": fact.statement,
        "subject": fact.subject_surface,
        "predicate": fact.predicate_surface,
        "measure_id": fact.measure_id,
        "measure": fact.measure.name if fact.measure else None,
        "entity_id": fact.entity_id,
        "entity": fact.entity.name if fact.entity else None,
        "value_text": fact.value_text,
        "value_number": fact.value_number,
        "value_base": fact.value_base,
        "unit_surface": fact.unit_surface,
        "unit": fact.unit_canonical,
        "unit_class": fact.unit_class,
        "currency": fact.currency,
        "period_label": fact.period_label,
        "period_kind": fact.period_kind,
        "period_start": fact.period_start,
        "period_end": fact.period_end,
        "qualifiers": fact.qualifiers or {},
        "basis": fact.basis,
        "direction": fact.direction,
        "confidence": round(fact.confidence, 3),
        "evidence": {
            "quote": fact.evidence_quote,
            "start": fact.evidence_start,
            "end": fact.evidence_end,
            "score": round(fact.evidence_score, 1),
            "exact": fact.evidence_exact,
            "bboxes": fact.bboxes or [],
        },
        "source": {
            "document_id": fact.document_id,
            "document_title": (document.title or document.filename) if document else None,
            "publisher": document.publisher if document else None,
            "page_number": page.page_number if page else None,
            "printed_label": page.printed_label if page else None,
            "page_width": page.width if page else None,
            "page_height": page.height if page else None,
        },
    }


def fact_detail(
    fact: Fact,
    document: Document | None,
    page: Page | None,
    relations: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = fact_summary(fact, document=document, page=page)
    payload["extractor"] = fact.extractor
    payload["model"] = fact.model
    payload["issues"] = (fact.raw or {}).get("issues", [])
    payload["used_vision"] = (fact.raw or {}).get("used_vision", False)
    payload["relations"] = relations
    return payload


def relation_summary(
    relation: Relation,
    left: dict[str, Any],
    right: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": relation.id,
        "type": relation.relation_type,
        "subtype": relation.subtype,
        "dimension": relation.dimension,
        "explanation": relation.explanation,
        "decided_by": relation.decided_by,
        "rule_id": relation.rule_id,
        "confidence": round(relation.confidence, 3),
        "severity": round(relation.severity, 3),
        "delta_absolute": relation.delta_absolute,
        "delta_relative": relation.delta_relative,
        "cross_document": relation.cross_document,
        "similarity": relation.similarity,
        "superseded_fact_id": (relation.raw or {}).get("superseded_fact_id"),
        "order_sensitive": bool((relation.raw or {}).get("order_sensitive")),
        "reverse_relation_type": (relation.raw or {}).get("reverse_relation_type"),
        "left": left,
        "right": right,
    }


def measure_summary(measure: Measure, first_seen: str | None = None) -> dict[str, Any]:
    return {
        "id": measure.id,
        "slug": measure.slug,
        "name": measure.name,
        "description": measure.description,
        "unit_class": measure.unit_class,
        "aliases": list(measure.aliases or []),
        "alias_count": len(measure.aliases or []),
        "fact_count": measure.fact_count,
        "document_count": measure.document_count,
        "first_seen_document_id": measure.first_seen_document_id,
        "first_seen_document": first_seen,
        "created_at": measure.created_at.isoformat() if measure.created_at else None,
    }


def rejection_summary(rejection: Rejection, document: Document | None = None) -> dict[str, Any]:
    return {
        "id": rejection.id,
        "document_id": rejection.document_id,
        "document_title": (document.title or document.filename) if document else None,
        "page_number": rejection.page_number,
        "stage": rejection.stage,
        "reason": rejection.reason,
        "detail": rejection.detail,
        "candidate": rejection.candidate or {},
        "created_at": rejection.created_at.isoformat() if rejection.created_at else None,
    }


def job_summary(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "document_id": job.document_id,
        "stage": job.stage,
        "progress": round(job.progress, 4),
        "message": job.message,
        "error": job.error,
        "stats": job.stats or {},
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }
