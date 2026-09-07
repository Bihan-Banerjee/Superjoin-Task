"""Relational schema for the knowledge layer.

Design notes worth stating up front:

* Facts carry both their *surface* form (what the document said) and their *normalised*
  form (what it means). Losing either one breaks something: without the surface the
  evidence stops being verifiable, without the normal form nothing is comparable.
* The measure and entity registries are data, not code. New kinds of facts create new
  rows rather than requiring a schema migration, which is what lets the layer absorb a
  document type it has never seen.
* Rejections are stored, not logged. A fact the pipeline threw away is the most useful
  thing it can tell you about its own reliability.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


# --- Status and vocabulary constants ---------------------------------------------------
# Kept as plain strings rather than SQL enums: the relation vocabulary is expected to grow,
# and SQLite enum changes would mean a table rewrite for what is really just a new label.

DOC_STATUS_PENDING = "pending"
DOC_STATUS_PROCESSING = "processing"
DOC_STATUS_READY = "ready"
DOC_STATUS_FAILED = "failed"

JOB_STAGES = (
    "queued",
    "parsing",
    "profiling",
    "extracting",
    "grounding",
    "normalising",
    "registering",
    "linking",
    "done",
)

PAGE_PROSE = "prose"
PAGE_TABLE = "table"
PAGE_CHART = "chart_slide"
PAGE_MIXED = "mixed"
PAGE_TOC = "toc"
PAGE_BOILERPLATE = "boilerplate"
PAGE_EMPTY = "empty"

FACT_QUANTITATIVE = "quantitative"
FACT_TEMPORAL = "temporal"
FACT_CATEGORICAL = "categorical"
FACT_RELATIONAL = "relational"
FACT_DEFINITIONAL = "definitional"

REL_CORROBORATES = "corroborates"
REL_CONTRADICTS = "contradicts"
REL_RECONCILED = "reconciled_by_context"
REL_REFINES = "refines"
REL_SUPERSEDES = "supersedes"

DIM_PERIOD = "period"
DIM_UNIT_SCALE = "unit_scale"
DIM_CURRENCY = "currency"
DIM_SCOPE = "scope"
DIM_SEGMENT = "segment"
DIM_BASIS = "basis"
DIM_VINTAGE = "vintage"
DIM_ENTITY = "entity"
DIM_DEFINITION = "definition"
DIM_NONE = "none"

DECIDED_BY_RULE = "rule"
DECIDED_BY_MODEL = "model"


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    filename: Mapped[str] = mapped_column(String(512))
    stored_path: Mapped[str] = mapped_column(String(1024))
    byte_size: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[int] = mapped_column(Integer, default=0)

    # Profiled from the document itself, never from its filename.
    title: Mapped[str | None] = mapped_column(String(1024))
    publisher: Mapped[str | None] = mapped_column(String(512))
    doc_type: Mapped[str | None] = mapped_column(String(128))
    subject_entity: Mapped[str | None] = mapped_column(String(512))
    as_of_date: Mapped[str | None] = mapped_column(String(32))
    published_date: Mapped[str | None] = mapped_column(String(32))
    period_label: Mapped[str | None] = mapped_column(String(128))
    default_currency: Mapped[str | None] = mapped_column(String(8))
    default_scale: Mapped[float | None] = mapped_column(Float)
    fiscal_convention: Mapped[str] = mapped_column(String(32), default="calendar")
    reporting_basis: Mapped[str | None] = mapped_column(String(64))
    language: Mapped[str | None] = mapped_column(String(16))

    status: Mapped[str] = mapped_column(String(32), default=DOC_STATUS_PENDING, index=True)
    error: Mapped[str | None] = mapped_column(Text)
    profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    pages: Mapped[list[Page]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )
    facts: Mapped[list[Fact]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class Page(Base):
    __tablename__ = "pages"
    __table_args__ = (
        UniqueConstraint("document_id", "page_number", name="uq_page_per_document"),
        Index("ix_pages_document_type", "document_id", "page_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    page_number: Mapped[int] = mapped_column(Integer)
    printed_label: Mapped[str | None] = mapped_column(String(64))
    page_type: Mapped[str] = mapped_column(String(32), default=PAGE_PROSE, index=True)

    text: Mapped[str] = mapped_column(Text, default="")
    text_sha: Mapped[str] = mapped_column(String(64), index=True)
    rendition: Mapped[str] = mapped_column(Text, default="")
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    char_count: Mapped[int] = mapped_column(Integer, default=0)

    width: Mapped[float] = mapped_column(Float, default=0.0)
    height: Mapped[float] = mapped_column(Float, default=0.0)
    unit_currency: Mapped[str | None] = mapped_column(String(8))
    unit_scale: Mapped[float | None] = mapped_column(Float)
    layout: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    extracted: Mapped[bool] = mapped_column(Boolean, default=False)

    document: Mapped[Document] = relationship(back_populates="pages")
    facts: Mapped[list[Fact]] = relationship(
        back_populates="page", cascade="all, delete-orphan", passive_deletes=True
    )


class Measure(Base):
    """A canonical thing that can be measured. Grows as documents introduce new ones."""

    __tablename__ = "measures"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text)
    unit_class: Mapped[str | None] = mapped_column(String(64), index=True)
    aliases: Mapped[list[Any]] = mapped_column(JSON, default=list)
    embedding: Mapped[bytes | None] = mapped_column()
    fact_count: Mapped[int] = mapped_column(Integer, default=0)
    document_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Entity(Base):
    """A canonical subject. Companies, institutions, countries, people, places."""

    __tablename__ = "entities"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(512))
    entity_type: Mapped[str | None] = mapped_column(String(64), index=True)
    aliases: Mapped[list[Any]] = mapped_column(JSON, default=list)
    embedding: Mapped[bytes | None] = mapped_column()
    fact_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class QualifierKey(Base):
    """A qualifier dimension discovered in the corpus, e.g. segment, basis, geography.

    Tracked separately from facts so the UI can show the schema widening over time, and so
    the reconciler knows which keys have ever been observed to discriminate between values.
    """

    __tablename__ = "qualifier_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    values: Mapped[list[Any]] = mapped_column(JSON, default=list)
    fact_count: Mapped[int] = mapped_column(Integer, default=0)
    discriminating: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Fact(Base):
    __tablename__ = "facts"
    __table_args__ = (
        Index("ix_facts_measure_period", "measure_id", "period_start", "period_end"),
        Index("ix_facts_entity_measure", "entity_id", "measure_id"),
        Index("ix_facts_document_page", "document_id", "page_id"),
        Index("ix_facts_claim_key", "claim_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"), index=True)

    kind: Mapped[str] = mapped_column(String(32), default=FACT_QUANTITATIVE, index=True)
    statement: Mapped[str] = mapped_column(Text)

    subject_surface: Mapped[str] = mapped_column(String(512))
    entity_id: Mapped[int | None] = mapped_column(
        ForeignKey("entities.id", ondelete="SET NULL"), index=True
    )
    predicate_surface: Mapped[str] = mapped_column(String(512))
    measure_id: Mapped[int | None] = mapped_column(
        ForeignKey("measures.id", ondelete="SET NULL"), index=True
    )

    value_text: Mapped[str | None] = mapped_column(String(512))
    value_number: Mapped[float | None] = mapped_column(Float)
    value_base: Mapped[float | None] = mapped_column(Float, index=True)
    unit_surface: Mapped[str | None] = mapped_column(String(128))
    unit_canonical: Mapped[str | None] = mapped_column(String(128))
    unit_class: Mapped[str | None] = mapped_column(String(64), index=True)
    unit_scale: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(8), index=True)

    period_label: Mapped[str | None] = mapped_column(String(256))
    period_kind: Mapped[str | None] = mapped_column(String(32))
    period_start: Mapped[str | None] = mapped_column(String(16), index=True)
    period_end: Mapped[str | None] = mapped_column(String(16), index=True)

    qualifiers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    basis: Mapped[str | None] = mapped_column(String(64), index=True)
    direction: Mapped[str | None] = mapped_column(String(32))

    evidence_quote: Mapped[str] = mapped_column(Text)
    evidence_start: Mapped[int | None] = mapped_column(Integer)
    evidence_end: Mapped[int | None] = mapped_column(Integer)
    evidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_exact: Mapped[bool] = mapped_column(Boolean, default=False)
    bboxes: Mapped[list[Any]] = mapped_column(JSON, default=list)

    confidence: Mapped[float] = mapped_column(Float, default=0.5, index=True)
    claim_key: Mapped[str] = mapped_column(String(1024), default="")
    extractor: Mapped[str] = mapped_column(String(64), default="")
    model: Mapped[str | None] = mapped_column(String(128))
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    document: Mapped[Document] = relationship(back_populates="facts")
    page: Mapped[Page] = relationship(back_populates="facts")
    measure: Mapped[Measure | None] = relationship()
    entity: Mapped[Entity | None] = relationship()


class FactEmbedding(Base):
    __tablename__ = "fact_embeddings"

    fact_id: Mapped[int] = mapped_column(
        ForeignKey("facts.id", ondelete="CASCADE"), primary_key=True
    )
    dimensions: Mapped[int] = mapped_column(Integer)
    vector: Mapped[bytes] = mapped_column()


class Relation(Base):
    __tablename__ = "relations"
    __table_args__ = (
        UniqueConstraint("left_fact_id", "right_fact_id", name="uq_relation_pair"),
        Index("ix_relations_type_dimension", "relation_type", "dimension"),
        Index("ix_relations_severity", "severity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    left_fact_id: Mapped[int] = mapped_column(
        ForeignKey("facts.id", ondelete="CASCADE"), index=True
    )
    right_fact_id: Mapped[int] = mapped_column(
        ForeignKey("facts.id", ondelete="CASCADE"), index=True
    )

    relation_type: Mapped[str] = mapped_column(String(32), index=True)
    subtype: Mapped[str | None] = mapped_column(String(64))
    dimension: Mapped[str] = mapped_column(String(32), default=DIM_NONE, index=True)
    explanation: Mapped[str] = mapped_column(Text, default="")

    decided_by: Mapped[str] = mapped_column(String(16), default=DECIDED_BY_RULE, index=True)
    rule_id: Mapped[str | None] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    severity: Mapped[float] = mapped_column(Float, default=0.0)

    delta_absolute: Mapped[float | None] = mapped_column(Float)
    delta_relative: Mapped[float | None] = mapped_column(Float)
    cross_document: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    similarity: Mapped[float | None] = mapped_column(Float)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    left_fact: Mapped[Fact] = relationship(foreign_keys=[left_fact_id])
    right_fact: Mapped[Fact] = relationship(foreign_keys=[right_fact_id])


class Rejection(Base):
    """A candidate fact the pipeline refused to keep, and why.

    This is deliberately a table and not a log line. It is the honest measure of how much
    the extractor got wrong, it drives the evaluation page, and it is where the required
    "extraction failure" case comes from.
    """

    __tablename__ = "rejections"
    __table_args__ = (Index("ix_rejections_document_reason", "document_id", "reason"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    page_id: Mapped[int | None] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"))
    page_number: Mapped[int | None] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(32), index=True)
    reason: Mapped[str] = mapped_column(String(64), index=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    candidate: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class LlmCall(Base):
    """Per-call accounting. Makes the cost and cache-hit story measurable rather than claimed."""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    purpose: Mapped[str] = mapped_column(String(32), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    prompt_sha: Mapped[str] = mapped_column(String(64), index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cached: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Setting(Base):
    """Small key/value store for pipeline state that must survive a restart."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
