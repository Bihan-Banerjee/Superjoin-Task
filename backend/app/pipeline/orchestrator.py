"""The ingest pipeline.

Runs a document through parse, classify, layout, profile, extract, ground, normalise,
register and link, reporting progress as it goes.

Two properties this is built around.

**Incremental.** Adding a document never rebuilds what is already there. Pages are stored
with a content hash, model responses are cached by request, and the linking stage only
pairs the new document's facts against the existing corpus. Re-ingesting an unchanged
document is close to free and produces an identical result.

**Attributable.** Every stage writes what it discarded and why. A page whose extraction
call failed, a quote that could not be verified, a value the extractor declined to attach:
all of them end up in the rejection ledger rather than disappearing into a log line.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.hashing import sha256_text
from app.db.engine import session_scope
from app.db.models import (
    DIM_PERIOD,
    DOC_STATUS_FAILED,
    DOC_STATUS_PROCESSING,
    DOC_STATUS_READY,
    REL_RECONCILED,
    Document,
    Fact,
    Job,
    LlmCall,
    Measure,
    Page,
    Rejection,
    Relation,
)
from app.llm.client import LlmClient
from app.pipeline import candidates as candidate_stage
from app.pipeline import extract as extract_stage
from app.pipeline.adjudicate import (
    AdjudicationRequest,
    CrossCheckStats,
    adjudicate_all,
    load_adjudication_context,
)
from app.pipeline.canonicalize import (
    EntityRegistry,
    MeasureRegistry,
    record_qualifier_keys,
)
from app.pipeline.classify import PageSignals, classify_page
from app.pipeline.ground import ground_candidates, unattributed_rejections
from app.pipeline.layout import reconstruct
from app.pipeline.normalize import (
    DocumentContext,
    PageContext,
    document_context_from_profile,
    normalize_candidate,
)
from app.pipeline.parse import ParsedDocument, find_quote_boxes, parse_pdf
from app.pipeline.reconcile import Verdict, reconcile

logger = logging.getLogger(__name__)

STAGE_WEIGHTS = {
    "parsing": 0.10,
    "profiling": 0.05,
    "extracting": 0.55,
    "grounding": 0.10,
    "registering": 0.05,
    "linking": 0.15,
}


@dataclass
class IngestStats:
    pages_total: int = 0
    pages_extracted: int = 0
    pages_skipped: int = 0
    pages_failed: int = 0
    facts_proposed: int = 0
    facts_kept: int = 0
    rejections: int = 0
    relations_created: int = 0
    adjudicated: int = 0
    cross_check: dict[str, Any] = field(default_factory=dict)
    registry: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        grounding_rate = (
            round(self.facts_kept / self.facts_proposed, 3) if self.facts_proposed else 0.0
        )
        return {
            "pages_total": self.pages_total,
            "pages_extracted": self.pages_extracted,
            "pages_skipped": self.pages_skipped,
            "pages_failed": self.pages_failed,
            "facts_proposed": self.facts_proposed,
            "facts_kept": self.facts_kept,
            "grounding_pass_rate": grounding_rate,
            "rejections": self.rejections,
            "relations_created": self.relations_created,
            "adjudicated_by_model": self.adjudicated,
            "adjudication_cross_check": self.cross_check,
            "registry": self.registry,
            "usage": self.usage,
        }


class ProgressReporter:
    """Writes stage and progress to the job row so the API can stream it."""

    def __init__(self, job_id: int) -> None:
        self._job_id = job_id
        self._base = 0.0

    def stage(self, name: str, message: str = "") -> None:
        self._base = sum(
            weight
            for stage, weight in STAGE_WEIGHTS.items()
            if _stage_order(stage) < _stage_order(name)
        )
        self._write(name, self._base, message)

    def within(self, name: str, done: int, total: int, message: str = "") -> None:
        weight = STAGE_WEIGHTS.get(name, 0.0)
        fraction = (done / total) if total else 1.0
        self._write(name, self._base + weight * fraction, message)

    def _write(self, stage: str, progress: float, message: str) -> None:
        with session_scope() as session:
            job = session.get(Job, self._job_id)
            if job is None:
                return
            job.stage = stage
            job.progress = round(min(max(progress, 0.0), 1.0), 4)
            if message:
                job.message = message
            session.add(job)


_STAGE_ORDER = ["parsing", "profiling", "extracting", "grounding", "registering", "linking", "done"]


def _stage_order(name: str) -> int:
    return _STAGE_ORDER.index(name) if name in _STAGE_ORDER else len(_STAGE_ORDER)


async def ingest_document(
    document_id: int,
    job_id: int,
    settings: Settings | None = None,
    client: LlmClient | None = None,
) -> IngestStats:
    """Run the pipeline over one document.

    `client` is injectable so a caller can reuse one across a batch, and so the integration
    tests can drive the real pipeline against a stub model rather than the network.
    """
    settings = settings or get_settings()
    for warning in settings.throttle_warnings():
        logger.warning("throttle: %s", warning)

    reporter = ProgressReporter(job_id)
    stats = IngestStats()
    owns_client = client is None
    client = client or LlmClient(settings)

    try:
        stats = await _run(document_id, job_id, settings, reporter, client, stats)
    except Exception as error:
        logger.exception("ingest failed for document %s", document_id)
        _mark_failed(document_id, job_id, str(error))
        raise
    finally:
        if owns_client:
            await client.aclose()

    return stats


async def _run(
    document_id: int,
    job_id: int,
    settings: Settings,
    reporter: ProgressReporter,
    client: LlmClient,
    stats: IngestStats,
) -> IngestStats:
    with session_scope() as session:
        document = session.get(Document, document_id)
        if document is None:
            raise ValueError(f"document {document_id} does not exist")
        document.status = DOC_STATUS_PROCESSING
        document.error = None
        session.add(document)
        path = Path(document.stored_path)
        filename = document.filename

    # --- parse ---------------------------------------------------------------------
    reporter.stage("parsing", f"reading {filename}")
    parsed = await asyncio.to_thread(
        parse_pdf,
        path,
        workers=settings.resolved_parse_workers,
        ocr=settings.enable_ocr,
        ocr_language=settings.ocr_language,
        ocr_dpi=settings.ocr_dpi,
    )
    stats.pages_total = len(parsed.pages)

    signals: dict[int, PageSignals] = {}
    renditions: dict[int, str] = {}
    for index, page in enumerate(parsed.pages):
        signal = classify_page(page)
        signals[page.page_number] = signal
        renditions[page.page_number] = (
            reconstruct(page, signal.page_type).rendition if signal.should_extract else ""
        )
        if index % 20 == 0:
            reporter.within("parsing", index + 1, len(parsed.pages))

    stats.pages_skipped = sum(1 for signal in signals.values() if not signal.should_extract)
    _store_pages(document_id, parsed, signals, renditions)

    # --- profile -------------------------------------------------------------------
    reporter.stage("profiling", "reading document conventions")
    profile = await extract_stage.profile_document(client, parsed, renditions)
    context = document_context_from_profile(profile)
    _store_profile(document_id, parsed, profile, context)
    document_context = extract_stage.build_document_context(profile, parsed)

    # --- extract -------------------------------------------------------------------
    reporter.stage("extracting", "reading pages")
    results = await extract_stage.extract_document(
        client,
        parsed,
        signals,
        renditions,
        document_context,
        settings,
        on_progress=lambda done, total: reporter.within(
            "extracting", done, total, f"read {done} of {total} page groups"
        ),
    )
    stats.pages_extracted = sum(1 for result in results.values() if result.ok)
    stats.pages_failed = sum(1 for result in results.values() if not result.ok)

    # --- ground and normalise ------------------------------------------------------
    reporter.stage("grounding", "verifying evidence")
    new_fact_ids = _persist_facts(document_id, path, parsed, results, context, stats, reporter)

    # --- register ------------------------------------------------------------------
    reporter.stage("registering", "resolving measures and entities")
    await _register(document_id, new_fact_ids, client, stats)

    # --- link ----------------------------------------------------------------------
    reporter.stage("linking", "comparing against the existing corpus")
    await _link(document_id, new_fact_ids, client, stats, reporter, settings)

    stats.usage = client.ledger.summary()
    _finish(document_id, job_id, client, stats)
    return stats


def _store_pages(
    document_id: int,
    parsed: ParsedDocument,
    signals: dict[int, PageSignals],
    renditions: dict[int, str],
) -> None:
    with session_scope() as session:
        session.execute(delete(Page).where(Page.document_id == document_id))
        session.flush()
        for page in parsed.pages:
            signal = signals[page.page_number]
            session.add(
                Page(
                    document_id=document_id,
                    page_number=page.page_number,
                    printed_label=page.printed_label,
                    page_type=signal.page_type,
                    text=page.text,
                    text_sha=sha256_text(page.text),
                    rendition=renditions.get(page.page_number, ""),
                    word_count=page.word_count,
                    char_count=page.char_count,
                    width=page.width,
                    height=page.height,
                    unit_currency=page.unit_currency,
                    unit_scale=page.unit_scale,
                    extracted=signal.should_extract,
                    ocr_applied=page.ocr_applied,
                    layout={"reason": signal.reason, "metrics": signal.metrics},
                )
            )
        session.flush()
        _record_text_layer(session, document_id, parsed)
        _record_content_overlap(session, document_id)


def _record_text_layer(session: Session, document_id: int, parsed: ParsedDocument) -> None:
    """Count the pages there was nothing to read on, and say so if that is most of them.

    A document made of scans ingests without error and produces no facts. Without this it is
    indistinguishable from a document that simply states nothing, which is the one failure
    mode most likely to be mistaken for a bug.
    """
    scanned = sum(1 for page in parsed.pages if page.looks_scanned and not page.ocr_applied)
    recovered = sum(1 for page in parsed.pages if page.ocr_applied)
    document = session.get(Document, document_id)
    if document is None:
        return
    document.scanned_pages = scanned
    document.ocr_pages = recovered

    if scanned and parsed.pages and scanned / len(parsed.pages) >= 0.5:
        logger.warning(
            "document %d has no usable text layer on %d of %d pages%s",
            document_id,
            scanned,
            len(parsed.pages),
            "" if recovered else "; set ENABLE_OCR=true to read them",
        )


# Pages shorter than this are dropped before the overlap is measured. A cover sheet, a
# divider or a page of nothing but a running head is identical across unrelated documents
# from the same publisher, and counting those would report every filing as a near-duplicate
# of every other.
_OVERLAP_MINIMUM_PAGE_CHARS = 400


def _record_content_overlap(session: Session, document_id: int) -> None:
    """How much of this document was already in the layer, and where.

    Upload refuses a byte-identical file on its sha256, which catches the same file twice
    and nothing else. The same content routinely arrives as a different file: a PDF
    re-exported by a different tool, a report re-downloaded after a cosmetic revision, an
    excerpt of something already ingested. Page text hashes catch those, because the words
    on the page do not change when the file around them does.

    Measured as containment (how much of the *new* document is already present) rather
    than as a symmetric overlap. The question being asked is "have I seen this before", and
    a ten-page excerpt of a hundred-page filing is entirely contained in it while sharing
    only a tenth of its pages.

    Nothing is skipped on the strength of this. A revised filing shares most of its pages
    with the version it replaces, and the handful that changed are the reason to ingest it.
    Re-reading the shared pages is nearly free in any case: the response cache is keyed on
    prompt content, so a page whose text is unchanged is served from disk rather than
    re-extracted.
    """
    mine = {
        sha
        for sha, chars in session.execute(
            select(Page.text_sha, Page.char_count).where(Page.document_id == document_id)
        )
        if chars >= _OVERLAP_MINIMUM_PAGE_CHARS
    }
    if not mine:
        return

    counts: dict[int, int] = {}
    rows = session.execute(
        select(Page.document_id, Page.text_sha)
        .where(Page.document_id != document_id)
        .where(Page.text_sha.in_(mine))
        .where(Page.char_count >= _OVERLAP_MINIMUM_PAGE_CHARS)
        .distinct()
    )
    for other_id, _ in rows:
        counts[other_id] = counts.get(other_id, 0) + 1
    if not counts:
        return

    best_id, shared = max(counts.items(), key=lambda item: item[1])
    overlap = shared / len(mine)
    document = session.get(Document, document_id)
    if document is None:
        return
    document.content_overlap = round(overlap, 4)
    document.near_duplicate_of = best_id if overlap >= get_settings().near_duplicate_ratio else None
    if document.near_duplicate_of is not None:
        logger.warning(
            "document %d repeats %.0f%% of document %d's pages",
            document_id,
            overlap * 100,
            best_id,
        )


def _store_profile(
    document_id: int,
    parsed: ParsedDocument,
    profile: dict[str, Any],
    context: DocumentContext,
) -> None:
    with session_scope() as session:
        document = session.get(Document, document_id)
        if document is None:
            return
        document.page_count = parsed.page_count
        document.title = str(profile.get("title") or "").strip() or document.filename
        document.publisher = str(profile.get("publisher") or "").strip() or None
        document.doc_type = str(profile.get("doc_type") or "").strip() or None
        document.subject_entity = str(profile.get("subject_entity") or "").strip() or None
        document.as_of_date = context.as_of_date
        document.published_date = str(profile.get("published_date") or "").strip() or None
        document.period_label = str(profile.get("period_label") or "").strip() or None
        document.default_currency = context.default_currency
        document.default_scale = context.default_scale
        document.fiscal_convention = context.fiscal_convention
        document.reporting_basis = context.reporting_basis
        document.language = str(profile.get("language") or "").strip() or None
        document.profile = profile
        session.add(document)


def _persist_facts(
    document_id: int,
    path: Path,
    parsed: ParsedDocument,
    results: dict[int, extract_stage.PageResult],
    context: DocumentContext,
    stats: IngestStats,
    reporter: ProgressReporter,
) -> list[int]:
    pages_by_number = {page.page_number: page for page in parsed.pages}
    new_ids: list[int] = []

    with session_scope() as session:
        session.execute(delete(Fact).where(Fact.document_id == document_id))
        session.execute(delete(Rejection).where(Rejection.document_id == document_id))
        page_rows = {
            row.page_number: row.id
            for row in session.scalars(select(Page).where(Page.document_id == document_id))
        }

    # One transaction per page rather than one for the document. SQLite permits a single
    # writer, and the progress reporter is a writer too, so holding a transaction open
    # across the whole loop deadlocks against the reporter's own commit. Committing per page
    # also means progress is visible to readers as the ingest runs rather than all at once.
    for index, (page_number, result) in enumerate(sorted(results.items())):
        page_id = page_rows.get(page_number)
        parsed_page = pages_by_number.get(page_number)
        if page_id is None or parsed_page is None:
            continue

        with session_scope() as session:
            if not result.ok:
                session.add(
                    Rejection(
                        document_id=document_id,
                        page_id=page_id,
                        page_number=page_number,
                        stage="extraction",
                        reason="extraction_call_failed",
                        detail=result.error or "the model call did not complete",
                        candidate={},
                    )
                )
                stats.rejections += 1
                continue

            stats.facts_proposed += len(result.facts)
            outcome = ground_candidates(result.facts, parsed_page.text)

            for rejection in [*outcome.rejected, *unattributed_rejections(result.unattributed)]:
                session.add(
                    Rejection(
                        document_id=document_id,
                        page_id=page_id,
                        page_number=page_number,
                        stage="grounding",
                        reason=rejection.reason,
                        detail=rejection.detail,
                        candidate=rejection.candidate,
                    )
                )
                stats.rejections += 1

            page_context = PageContext(
                unit_currency=parsed_page.unit_currency, unit_scale=parsed_page.unit_scale
            )
            for grounded in outcome.grounded:
                normalised = normalize_candidate(
                    grounded.candidate, grounded.quote, context, page_context
                )
                fact = Fact(
                    document_id=document_id,
                    page_id=page_id,
                    kind=normalised.kind,
                    statement=normalised.statement,
                    subject_surface=normalised.subject,
                    predicate_surface=normalised.predicate,
                    value_text=normalised.value_text,
                    value_number=normalised.value_number,
                    value_base=normalised.value_base,
                    unit_surface=normalised.unit_surface,
                    unit_canonical=normalised.unit.canonical if normalised.unit else None,
                    unit_class=normalised.unit_class,
                    unit_scale=normalised.unit.factor if normalised.unit else None,
                    currency=normalised.currency,
                    period_label=normalised.period_label,
                    period_kind=normalised.period.kind if normalised.period.resolved else None,
                    period_start=(
                        normalised.period.start.isoformat() if normalised.period.resolved else None
                    ),
                    period_end=(
                        normalised.period.end.isoformat() if normalised.period.resolved else None
                    ),
                    qualifiers=normalised.qualifiers,
                    basis=normalised.basis,
                    direction=normalised.direction,
                    evidence_quote=grounded.quote,
                    evidence_start=grounded.start,
                    evidence_end=grounded.end,
                    evidence_score=grounded.score,
                    evidence_exact=grounded.exact,
                    bboxes=find_quote_boxes(path, page_number, grounded.quote),
                    confidence=normalised.confidence,
                    claim_key=normalised.claim_key(),
                    extractor=extract_stage.EXTRACTOR_VERSION,
                    model=result.model,
                    raw={
                        "candidate": grounded.candidate,
                        "issues": normalised.issues,
                        "used_vision": result.used_vision,
                    },
                )
                session.add(fact)
                session.flush()
                new_ids.append(fact.id)
                stats.facts_kept += 1
                record_qualifier_keys(session, normalised.qualifiers, document_id)

        if index % 10 == 0:
            reporter.within("grounding", index + 1, len(results))

    return new_ids


async def _register(
    document_id: int, fact_ids: list[int], client: LlmClient, stats: IngestStats
) -> None:
    if not fact_ids:
        return

    with session_scope() as session:
        facts = list(session.scalars(select(Fact).where(Fact.id.in_(fact_ids))))
        measures = MeasureRegistry(session, document_id=document_id)
        entities = EntityRegistry(session, document_id=document_id)

        surfaces: dict[str, tuple[str, str | None, str]] = {}
        for fact in facts:
            key = fact.predicate_surface
            if key and key not in surfaces:
                surfaces[key] = (key, fact.unit_class, fact.statement)

        resolved = await measures.resolve_batch(list(surfaces.values()), client)
        # Resolved by distinct subject, not per fact: a filing states hundreds of facts about
        # one company, and asking the same question once is the difference between this
        # stage taking seconds and taking longer than the extraction it follows.
        entity_by_surface = entities.resolve_many([fact.subject_surface for fact in facts])

        for fact in facts:
            measure = resolved.get(fact.predicate_surface)
            if measure is None:
                measure = measures.lookup_exact(fact.predicate_surface)
            if measure is not None:
                fact.measure_id = measure.id
                measure.fact_count = (measure.fact_count or 0) + 1
                if measure.unit_class is None and fact.unit_class:
                    measure.unit_class = fact.unit_class
                session.add(measure)

            entity = entity_by_surface.get(fact.subject_surface.strip())
            if entity is not None:
                fact.entity_id = entity.id
                entity.fact_count = (entity.fact_count or 0) + 1
                session.add(entity)
            session.add(fact)

        _refresh_measure_document_counts(session)
        report = measures.report
        report.entities_created = entities.report.entities_created
        report.entities_linked = entities.report.entities_linked
        stats.registry = report.as_dict()

    with session_scope() as session:
        facts = list(session.scalars(select(Fact).where(Fact.id.in_(fact_ids))))
        candidate_stage.store_embeddings(session, facts)


def _refresh_measure_document_counts(session: Session) -> None:
    from sqlalchemy import func

    counts = dict(
        session.execute(
            select(Fact.measure_id, func.count(func.distinct(Fact.document_id)))
            .where(Fact.measure_id.isnot(None))
            .group_by(Fact.measure_id)
        ).all()
    )
    for measure in session.scalars(select(Measure)):
        measure.document_count = int(counts.get(measure.id, 0))
        session.add(measure)


async def _link(
    document_id: int,
    fact_ids: list[int],
    client: LlmClient,
    stats: IngestStats,
    reporter: ProgressReporter,
    settings: Settings,
) -> None:
    if not fact_ids:
        return

    with session_scope() as session:
        pairs = candidate_stage.generate(session, fact_ids)
        facts = {
            fact.id: fact
            for fact in session.scalars(
                select(Fact).where(
                    Fact.id.in_(
                        {pair.left_id for pair in pairs} | {pair.right_id for pair in pairs}
                    )
                )
            )
        }

        settled: list[tuple[int, int, Verdict, float]] = []
        escalations: list[AdjudicationRequest] = []

        for pair in pairs:
            left, right = facts.get(pair.left_id), facts.get(pair.right_id)
            if left is None or right is None:
                continue
            verdict = reconcile(left, right)
            if verdict.escalate:
                escalations.append(
                    AdjudicationRequest(
                        left=left,
                        right=right,
                        note=verdict.note,
                        delta_absolute=verdict.delta_absolute,
                        delta_relative=verdict.delta_relative,
                    )
                )
            elif verdict.relation_type and _is_informative(verdict, left, right):
                settled.append((left.id, right.id, verdict, pair.similarity))

    verdicts: dict[tuple[int, int], Verdict] = {}
    if escalations:
        # Evidence and document context are gathered first and the session released, so the
        # long stretch of model calls holds no database transaction. Without that, progress
        # reporting during adjudication contends with the read transaction for the writer
        # lock, and a corpus large enough to escalate anything deadlocks.
        with session_scope() as session:
            context = load_adjudication_context(session, escalations)

        cross_check = CrossCheckStats()
        verdicts = await adjudicate_all(
            client,
            escalations,
            context,
            on_progress=lambda done, total: reporter.within(
                "linking", done, total, f"adjudicated {done} of {total} ambiguous pairs"
            ),
            cross_check=settings.adjudication_cross_check,
            stats=cross_check,
        )
        stats.adjudicated = len(verdicts)
        if cross_check.checked:
            stats.cross_check = cross_check.as_dict()

    with session_scope() as session:
        for left_id, right_id, verdict, similarity in settled:
            _store_relation(session, left_id, right_id, verdict, similarity, stats)
        for (left_id, right_id), verdict in verdicts.items():
            _store_relation(session, left_id, right_id, verdict, 0.0, stats)


def _is_informative(verdict: Verdict, left: Fact, right: Fact) -> bool:
    """Whether a relation says anything a reader could not already see.

    One case is filtered: two facts in the *same* document that differ only in period. A
    table listing FY22, FY23 and FY24 produces those by construction: three pairs per
    measure that report nothing except the shape of the table. On the earnings deck they
    were 195 of 271 relations and buried everything worth looking at.

    Across documents the same comparison is informative, because two publishers choosing
    different periods for the same measure is exactly the sort of thing this system exists
    to surface. Containment within one document is kept too: a quarter inside a year is a
    real relationship between two differently-scoped figures.
    """
    if left.document_id != right.document_id:
        return True
    return not (verdict.relation_type == REL_RECONCILED and verdict.dimension == DIM_PERIOD)


def _relation_detail(verdict: Verdict) -> dict[str, Any]:
    """The parts of a verdict that do not fit a column.

    Kept sparse on purpose: an empty dict for the ordinary case rather than a row of nulls,
    so what is present in `raw` is always something worth reading.
    """
    detail: dict[str, Any] = {}
    if verdict.superseded_fact_id is not None:
        detail["superseded_fact_id"] = verdict.superseded_fact_id
    if verdict.order_sensitive:
        detail["order_sensitive"] = True
        if verdict.reverse_relation_type:
            detail["reverse_relation_type"] = verdict.reverse_relation_type
    return detail


def _store_relation(
    session: Session,
    left_id: int,
    right_id: int,
    verdict: Verdict,
    similarity: float,
    stats: IngestStats,
) -> None:
    # Normalising pair order means the unique constraint catches a duplicate regardless of
    # which side was the newly ingested fact.
    left_id, right_id = (left_id, right_id) if left_id < right_id else (right_id, left_id)
    existing = session.scalar(
        select(Relation).where(Relation.left_fact_id == left_id, Relation.right_fact_id == right_id)
    )
    left = session.get(Fact, left_id)
    right = session.get(Fact, right_id)
    if left is None or right is None:
        return

    fields = {
        "relation_type": verdict.relation_type,
        "subtype": verdict.subtype,
        "dimension": verdict.dimension,
        "explanation": verdict.explanation,
        "decided_by": verdict.decided_by,
        "rule_id": verdict.rule_id,
        "confidence": verdict.confidence,
        "severity": verdict.severity,
        "delta_absolute": verdict.delta_absolute,
        "delta_relative": verdict.delta_relative,
        "cross_document": left.document_id != right.document_id,
        "similarity": similarity or None,
        "raw": _relation_detail(verdict),
    }

    if existing is not None:
        for key, value in fields.items():
            setattr(existing, key, value)
        session.add(existing)
        return

    session.add(Relation(left_fact_id=left_id, right_fact_id=right_id, **fields))
    stats.relations_created += 1


def _finish(document_id: int, job_id: int, client: LlmClient, stats: IngestStats) -> None:
    with session_scope() as session:
        for record in client.ledger.records:
            session.add(
                LlmCall(
                    job_id=job_id,
                    document_id=document_id,
                    purpose=record.purpose,
                    provider=record.provider,
                    model=record.model,
                    prompt_sha=record.prompt_sha,
                    input_tokens=record.input_tokens,
                    output_tokens=record.output_tokens,
                    latency_ms=record.latency_ms,
                    cached=record.cached,
                    attempts=record.attempts,
                    ok=record.ok,
                    error=record.error,
                )
            )

        document = session.get(Document, document_id)
        if document is not None:
            document.status = DOC_STATUS_READY
            session.add(document)

        job = session.get(Job, job_id)
        if job is not None:
            job.stage = "done"
            job.progress = 1.0
            job.message = (
                f"{stats.facts_kept} facts kept, {stats.relations_created} relations found"
            )
            job.stats = stats.as_dict()
            job.finished_at = datetime.now(UTC)
            session.add(job)


def _mark_failed(document_id: int, job_id: int, message: str) -> None:
    with session_scope() as session:
        document = session.get(Document, document_id)
        if document is not None:
            document.status = DOC_STATUS_FAILED
            document.error = message[:2000]
            session.add(document)
        job = session.get(Job, job_id)
        if job is not None:
            job.stage = "failed"
            job.error = message[:2000]
            job.finished_at = datetime.now(UTC)
            session.add(job)
