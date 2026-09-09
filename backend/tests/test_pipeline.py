"""End-to-end pipeline behaviour, driven against a stub model.

Everything the project actually wrote runs for real here: parsing, layout, classification,
grounding, normalisation, the registry, candidate generation and the rule engine. Only the
model is stubbed, which is what makes it possible to assert on exact outcomes: a real model
would make the expected fact count a moving target and the suite would stop being a test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.engine import session_scope
from app.db.models import (
    DOC_STATUS_PENDING,
    DOC_STATUS_READY,
    REL_CONTRADICTS,
    REL_CORROBORATES,
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
from app.pipeline.orchestrator import ingest_document
from tests.factories import DECK_PAGES, FILING_PAGES, REVISED_DECK_PAGES, build_pdf

FILING_PROFILE = {
    "title": "Delhivery Limited Annual Report FY24",
    "publisher": "Delhivery Limited",
    "doc_type": "annual_report",
    "subject_entity": "Delhivery Limited",
    "period_label": "FY24",
    "as_of_date": "2024-03-31",
    "default_currency": "INR",
    "default_scale": "million",
    "fiscal_convention": "india",
    "reporting_basis": "consolidated",
}

DECK_PROFILE = {
    "title": "Delhivery Q4 FY24 Earnings Presentation",
    "publisher": "Delhivery Limited",
    "doc_type": "earnings_presentation",
    "subject_entity": "Delhivery Limited",
    "period_label": "Q4 FY24",
    "default_currency": "INR",
    "default_scale": "crore",
    "fiscal_convention": "india",
}


def fact(**overrides):
    base = {
        "kind": "quantitative",
        "subject": "Delhivery Limited",
        "confidence": 0.9,
    }
    base.update(overrides)
    return base


FILING_FACTS = {
    "facts": [
        fact(
            statement="Revenue from services for FY24 was 81,419.7 million rupees",
            predicate="revenue from services",
            value_text="81,419.7",
            value_number=81419.7,
            unit="",
            currency="INR",
            period="year ended March 31, 2024",
            evidence_quote="Revenue from services for the year was 81,419.7",
        ),
        fact(
            statement="Express Parcel revenue for FY24 was 50,772.3 million rupees",
            predicate="express parcel revenue",
            value_text="50,772.3",
            value_number=50772.3,
            unit="",
            currency="INR",
            period="year ended March 31, 2024",
            qualifiers={"segment": "Express Parcel"},
            evidence_quote="Express Parcel revenue for the year was 50,772.3",
        ),
        fact(
            statement="Delhivery employed 61,527 people as at March 31, 2024",
            predicate="employee headcount",
            value_text="61,527",
            value_number=61527,
            unit="people",
            period="as at March 31, 2024",
            evidence_quote="The Group employed 61,527 people as at March 31, 2024",
        ),
        fact(
            statement="The registered office is at Plot 5, Sector 44, Gurugram, Haryana",
            kind="categorical",
            predicate="registered office address",
            value_text="Plot 5, Sector 44, Gurugram, Haryana",
            evidence_quote="Company is located at Plot 5, Sector 44, Gurugram, Haryana",
        ),
        # Wholly invented: no sentence resembling this appears on the page.
        fact(
            statement="The Board approved a dividend of 12 rupees per share",
            predicate="dividend per share",
            value_text="12",
            value_number=12,
            unit="rupees per share",
            period="FY24",
            evidence_quote="The Board approved a dividend of 12 rupees per share for the year",
        ),
        # A real sentence with the number swapped. Fuzzy location finds the sentence; the
        # value check is what catches it.
        fact(
            statement="Revenue from services for FY24 was 99,999.9 million rupees",
            predicate="revenue from services",
            value_text="99,999.9",
            value_number=99999.9,
            period="FY24",
            evidence_quote="Revenue from services for the year was 99,999.9",
        ),
        # Real quote, unrelated number.
        fact(
            statement="Adjusted EBITDA was 5,000.0 million rupees",
            predicate="adjusted ebitda",
            value_text="5,000.0",
            value_number=5000.0,
            period="FY24",
            evidence_quote="The Company is listed on",
        ),
    ],
    "unattributed": [
        {"value_text": "91", "reason": "could not tell which metric this counts"},
    ],
}

DECK_FACTS = {
    "facts": [
        fact(
            statement="Revenue from services for FY24 was 8,142 crore rupees",
            predicate="revenue from services",
            value_text="8,142",
            value_number=8142,
            unit="Cr",
            currency="INR",
            period="FY24",
            evidence_quote="FY24: 8,142",
        ),
        fact(
            statement="Revenue from services for FY23 was 7,224 crore rupees",
            predicate="revenue from services",
            value_text="7,224",
            value_number=7224,
            unit="Cr",
            currency="INR",
            period="FY23",
            evidence_quote="FY23: 7,224",
        ),
        fact(
            statement="Express Parcel shipments for FY24 were 740 million",
            predicate="express parcel shipments",
            value_text="740",
            value_number=740,
            unit="Mn shipments",
            period="FY24",
            evidence_quote="FY24: 740    FY23: 663",
        ),
    ]
}

REVISED_FACTS = {
    "facts": [
        fact(
            statement="Revenue from services for FY24 was 8,500 crore rupees on a restated basis",
            predicate="revenue from services",
            value_text="8,500",
            value_number=8500,
            unit="Cr",
            currency="INR",
            period="FY24",
            basis="restated",
            evidence_quote="Revenue from services for FY24 was Rs 8,500 Cr on a restated basis",
        ),
        fact(
            statement="Express Parcel shipments for FY24 were 812 million",
            predicate="express parcel shipments",
            value_text="812",
            value_number=812,
            unit="Mn",
            period="FY24",
            evidence_quote="Express Parcel shipments for FY24 were 812 Mn",
        ),
    ]
}


def register(path: Path) -> tuple[int, int]:
    from app.core.hashing import sha256_file

    with session_scope() as session:
        document = Document(
            sha256=sha256_file(path),
            filename=path.name,
            stored_path=str(path),
            byte_size=path.stat().st_size,
            status=DOC_STATUS_PENDING,
        )
        session.add(document)
        session.flush()
        job = Job(document_id=document.id, stage="queued")
        session.add(job)
        session.flush()
        return document.id, job.id


async def ingest(path: Path, settings, stub, profile: dict, facts: dict) -> None:
    stub.default_by_purpose["profile"] = profile
    stub.default_by_purpose["extract"] = facts
    document_id, job_id = register(path)
    client = LlmClient(settings, provider=stub)
    await ingest_document(document_id, job_id, settings, client=client)


@pytest.fixture(scope="class")
async def filing(workspace: Path, settings, database, stub):
    path = build_pdf(workspace / "filing.pdf", FILING_PAGES)
    await ingest(path, settings, stub, FILING_PROFILE, FILING_FACTS)
    return path


class TestSingleDocument:
    async def test_document_is_profiled_from_its_own_content(self, filing):
        with session_scope() as session:
            document = session.scalar(select(Document))
            assert document.status == DOC_STATUS_READY
            assert document.publisher == "Delhivery Limited"
            assert document.fiscal_convention == "india"
            assert document.default_currency == "INR"
            assert document.default_scale == pytest.approx(1e6)

    async def test_only_verifiable_facts_are_kept(self, filing):
        with session_scope() as session:
            statements = {fact.statement for fact in session.scalars(select(Fact))}
        assert any("81,419.7" in statement for statement in statements)
        assert not any("99,999.9" in statement for statement in statements)
        assert not any("5,000.0" in statement for statement in statements)

    async def test_discarded_candidates_are_recorded_with_a_reason(self, filing):
        with session_scope() as session:
            reasons = {rejection.reason for rejection in session.scalars(select(Rejection))}
        assert "quote_not_found" in reasons
        assert "value_absent_from_quote" in reasons
        assert "unattributed_by_model" in reasons

    async def test_evidence_resolves_to_a_span_and_a_rectangle(self, filing):
        with session_scope() as session:
            revenue = session.scalar(
                select(Fact).where(Fact.predicate_surface == "revenue from services")
            )
            page = session.get(Page, revenue.page_id)

        assert revenue.evidence_start is not None
        assert page.text[revenue.evidence_start : revenue.evidence_end] == revenue.evidence_quote
        assert revenue.bboxes, "the quote should resolve to at least one rectangle"

    async def test_page_declared_scale_is_applied_to_bare_figures(self, filing):
        with session_scope() as session:
            revenue = session.scalar(
                select(Fact).where(Fact.predicate_surface == "revenue from services")
            )
        assert revenue.unit_class == "currency"
        assert revenue.currency == "INR"
        assert revenue.value_base == pytest.approx(81419.7 * 1e6)

    async def test_declared_scale_is_not_applied_to_a_headcount(self, filing):
        with session_scope() as session:
            headcount = session.scalar(
                select(Fact).where(Fact.predicate_surface == "employee headcount")
            )
        assert headcount.unit_class == "count"
        assert headcount.value_base == pytest.approx(61527)

    async def test_periods_resolve_using_the_documents_convention(self, filing):
        with session_scope() as session:
            revenue = session.scalar(
                select(Fact).where(Fact.predicate_surface == "revenue from services")
            )
        assert revenue.period_start == "2023-04-01"
        assert revenue.period_end == "2024-03-31"

    async def test_non_numeric_facts_survive(self, filing):
        with session_scope() as session:
            address = session.scalar(select(Fact).where(Fact.kind == "categorical"))
        assert address is not None
        assert "Gurugram" in address.value_text

    async def test_measures_and_entities_are_registered(self, filing):
        with session_scope() as session:
            measures = {measure.name for measure in session.scalars(select(Measure))}
            facts = list(session.scalars(select(Fact)))
        assert "revenue from services" in measures
        assert all(fact.measure_id is not None for fact in facts)
        assert all(fact.entity_id is not None for fact in facts)

    async def test_the_job_reports_what_it_did(self, filing):
        with session_scope() as session:
            job = session.scalar(select(Job))
        assert job.stage == "done"
        assert job.progress == 1.0
        assert job.stats["facts_kept"] == 4
        assert 0 < job.stats["grounding_pass_rate"] < 1

    async def test_every_model_call_is_accounted_for(self, filing):
        with session_scope() as session:
            calls = list(session.scalars(select(LlmCall)))
        assert calls
        assert {call.purpose for call in calls} >= {"profile", "extract"}


class TestCrossDocument:
    @pytest.fixture(scope="class")
    async def corpus(self, workspace: Path, settings, database, stub, filing):
        deck = build_pdf(workspace / "deck.pdf", DECK_PAGES)
        await ingest(deck, settings, stub, DECK_PROFILE, DECK_FACTS)
        return deck

    async def test_the_same_amount_in_crore_and_millions_corroborates(self, corpus):
        """The central case: 8,142 Cr and 81,419.7 million are one figure."""
        with session_scope() as session:
            relations = [
                relation
                for relation in session.scalars(select(Relation))
                if relation.relation_type == REL_CORROBORATES and relation.cross_document
            ]
        assert relations, "the two statements of FY24 revenue should have been linked"
        assert any(relation.dimension == "unit_scale" for relation in relations)

    async def test_a_fabricated_figure_cannot_validate_against_its_own_quote(self, corpus):
        """A real sentence with the number swapped must not survive.

        The value is checked against the source page, never against the text the model
        supplied: otherwise a quote corroborates itself and the guard is decorative.
        """
        with session_scope() as session:
            values = {fact.value_text for fact in session.scalars(select(Fact))}
            reasons = [
                rejection.detail
                for rejection in session.scalars(select(Rejection))
                if rejection.reason == "value_absent_from_quote"
            ]
        assert "99,999.9" not in values
        assert any("99,999.9" in reason for reason in reasons)

    async def test_a_different_year_is_reconciled_rather_than_flagged(self, corpus):
        with session_scope() as session:
            relations = list(
                session.scalars(select(Relation).where(Relation.relation_type == REL_RECONCILED))
            )
        assert any(relation.dimension == "period" for relation in relations)

    async def test_differently_worded_measures_resolve_to_one_registry_entry(self, corpus):
        with session_scope() as session:
            measure = session.scalar(select(Measure).where(Measure.name == "revenue from services"))
        assert measure.document_count == 2

    async def test_facts_from_the_same_page_are_not_compared_with_each_other(self, corpus):
        with session_scope() as session:
            for relation in session.scalars(select(Relation)):
                left = session.get(Fact, relation.left_fact_id)
                right = session.get(Fact, relation.right_fact_id)
                assert left.page_id != right.page_id

    async def test_rule_decisions_carry_the_rule_that_made_them(self, corpus):
        with session_scope() as session:
            relations = list(session.scalars(select(Relation)))
        rule_backed = [relation for relation in relations if relation.decided_by == "rule"]
        assert rule_backed
        assert all(relation.rule_id for relation in rule_backed)
        assert all(relation.explanation for relation in relations)


class TestContradiction:
    @pytest.fixture(scope="class")
    async def corpus(self, workspace: Path, settings, database, stub, filing):
        revised = build_pdf(workspace / "revised.pdf", REVISED_DECK_PAGES)
        await ingest(
            revised,
            settings,
            stub,
            {**DECK_PROFILE, "title": "Investor Update"},
            REVISED_FACTS,
        )
        return revised

    async def test_a_genuine_disagreement_is_reported(self, corpus):
        """8,500 Cr against 81,419.7 million for the same period, on a stated basis."""
        with session_scope() as session:
            relations = list(session.scalars(select(Relation)))

        conflicting = [
            relation
            for relation in relations
            if relation.relation_type in {REL_CONTRADICTS, REL_RECONCILED}
            and relation.cross_document
        ]
        assert conflicting, "the disagreement on FY24 revenue should have been surfaced"

    async def test_a_restated_figure_is_reconciled_by_basis_not_called_a_contradiction(
        self, corpus
    ):
        with session_scope() as session:
            relations = [
                relation
                for relation in session.scalars(select(Relation))
                if relation.dimension in {"basis", "vintage"}
            ]
        assert relations, "the restatement should have been recognised as a basis difference"

    async def test_an_unexplained_gap_is_a_contradiction_with_severity(self, corpus):
        with session_scope() as session:
            contradictions = list(
                session.scalars(select(Relation).where(Relation.relation_type == REL_CONTRADICTS))
            )
        for relation in contradictions:
            assert relation.severity > 0
            assert relation.dimension == "none"


class TestIncrementalIngest:
    async def test_reprocessing_replaces_facts_rather_than_duplicating_them(
        self, settings, database, stub, filing
    ):
        """Reprocessing is the normal way to adopt a prompt or rule change."""
        with session_scope() as session:
            before = session.query(Fact).count()
            document = session.scalar(select(Document))
            job = Job(document_id=document.id, stage="queued")
            session.add(job)
            session.flush()
            document_id, job_id = document.id, job.id

        client = LlmClient(settings, provider=stub)
        await ingest_document(document_id, job_id, settings, client=client)

        with session_scope() as session:
            assert session.query(Fact).count() == before
            assert session.query(Document).count() == 1

    async def test_a_second_document_only_pairs_against_what_already_exists(
        self, workspace: Path, settings, database, stub, filing
    ):
        """Adding a document costs work proportional to that document, not the corpus."""
        with session_scope() as session:
            before_relations = session.query(Relation).count()

        deck = build_pdf(workspace / "deck.pdf", DECK_PAGES)
        await ingest(deck, settings, stub, DECK_PROFILE, DECK_FACTS)

        with session_scope() as session:
            after = list(session.scalars(select(Relation)))
            new_document = session.scalar(select(Document).where(Document.filename == "deck.pdf"))
            new_fact_ids = {
                fact.id
                for fact in session.scalars(select(Fact).where(Fact.document_id == new_document.id))
            }

        added = [relation for relation in after][before_relations:]
        assert added, "the second document should have produced new relations"
        for relation in added:
            assert (
                relation.left_fact_id in new_fact_ids or relation.right_fact_id in new_fact_ids
            ), "every new relation must involve the newly ingested document"


class TestFailureHandling:
    async def test_a_failed_page_is_recorded_rather_than_read_as_empty(
        self, workspace: Path, settings, database, stub
    ):
        path = build_pdf(workspace / "filing.pdf", FILING_PAGES)
        stub.fail_on = {"classified as prose"}
        stub.default_by_purpose["profile"] = FILING_PROFILE
        stub.default_by_purpose["extract"] = {"facts": []}

        document_id, job_id = register(path)
        client = LlmClient(settings, provider=stub)
        await ingest_document(document_id, job_id, settings, client=client)

        with session_scope() as session:
            rejections = list(
                session.scalars(
                    select(Rejection).where(Rejection.reason == "extraction_call_failed")
                )
            )
            document = session.get(Document, document_id)

        assert rejections, "a page whose call failed must be visible, not silently empty"
        assert document.status == DOC_STATUS_READY


class TestEntityMerging:
    """Facts about different entities must never be compared.

    An embedding score alone merged a UK subsidiary with its parent and moved 245 facts
    under the wrong name, so a merge also requires the same tokens once legal forms are
    stripped.
    """

    def test_legal_form_variants_are_one_entity(self):
        from app.pipeline.canonicalize import _same_organisation

        assert _same_organisation("Delhivery Limited", "Delhivery Ltd")
        assert _same_organisation("Reserve Bank of India", "The Reserve Bank of India")

    def test_a_qualified_subsidiary_is_a_different_entity(self):
        from app.pipeline.canonicalize import _same_organisation

        assert not _same_organisation("Delhivery Limited", "Delhivery Corp Limited, United Kingdom")
        assert not _same_organisation("Delhivery Limited", "Delhivery USA Inc")


class TestNearDuplicateDetection:
    """The same content arriving as a different file.

    Upload refuses a byte-identical PDF on its sha256. That catches re-uploading the same
    file and nothing else: a re-export, a re-download after a cosmetic change, or an
    excerpt of something already ingested all produce different bytes and identical words.
    """

    @pytest.fixture(scope="class")
    async def corpus(self, workspace: Path, settings, database, stub):
        await ingest(
            build_pdf(workspace / "original.pdf", FILING_PAGES),
            settings,
            stub,
            FILING_PROFILE,
            FILING_FACTS,
        )
        # Same words, different file. A hash of the bytes cannot see the relationship.
        await ingest(
            build_pdf(workspace / "re-export.pdf", FILING_PAGES),
            settings,
            stub,
            FILING_PROFILE,
            FILING_FACTS,
        )
        await ingest(
            build_pdf(workspace / "unrelated.pdf", DECK_PAGES),
            settings,
            stub,
            DECK_PROFILE,
            DECK_FACTS,
        )

    def test_a_re_export_is_flagged_against_the_document_it_repeats(self, corpus):
        with session_scope() as session:
            original = session.scalar(select(Document).where(Document.filename == "original.pdf"))
            copy = session.scalar(select(Document).where(Document.filename == "re-export.pdf"))

            assert original.sha256 != copy.sha256, (
                "the two files must differ, or this proves nothing"
            )
            assert copy.near_duplicate_of == original.id
            assert copy.content_overlap == 1.0

    def test_the_first_document_is_not_flagged_against_the_copy_that_followed_it(self, corpus):
        """Flagging is about what was already in the layer, so it points backwards only."""
        with session_scope() as session:
            original = session.scalar(select(Document).where(Document.filename == "original.pdf"))
            assert original.near_duplicate_of is None

    def test_an_unrelated_document_is_left_alone(self, corpus):
        with session_scope() as session:
            unrelated = session.scalar(select(Document).where(Document.filename == "unrelated.pdf"))
            assert unrelated.near_duplicate_of is None

    def test_the_copy_is_still_ingested_rather_than_skipped(self, corpus):
        """Detection reports; it does not decide. A revised filing shares most of its pages
        with the version it replaces, and the few that changed are the reason to read it."""
        with session_scope() as session:
            copy = session.scalar(select(Document).where(Document.filename == "re-export.pdf"))
            assert session.query(Fact).filter(Fact.document_id == copy.id).count() > 0
