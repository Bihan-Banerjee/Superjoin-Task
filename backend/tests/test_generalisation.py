"""A document unlike anything the project was built against.

The brief is explicit that the system will be tested on unseen PDFs and that nothing may key
off a filename, a schema or a document-specific rule. Every other test in this suite uses
Indian corporate filings, which is exactly the corpus the thresholds were tuned on — so on
its own the suite could pass while the pipeline quietly only worked on that corpus.

This one uses a water utility in a different country: a fiscal year running October to
September, amounts in US dollars and billions, measures that are not financial at all
(megalitres, connections, non-revenue water), and a document type never seen before. If any
part of the pipeline were specific to the starter set, this is where it would show.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.engine import session_scope
from app.db.models import REL_SUPERSEDES, Document, Fact, Measure, Relation
from tests.factories import UTILITY_PAGES, UTILITY_PROFILE, build_pdf
from tests.test_pipeline import ingest

# One flat response, the way the stub serves every extraction call. `page_number` is popped
# by the splitter and decides which page each fact is grounded against, which matters here
# because the restatement sits on a different page from the figure it restates.
UTILITY_FACTS = {
    "facts": [
        {
            "statement": "Capital expenditure for FY2024 was US$ 4.6 billion.",
            "kind": "quantitative",
            "page_number": 2,
            "subject": "Cascadia Water Authority",
            "predicate": "capital expenditure",
            "value_text": "US$ 4.6 billion",
            "value_number": 4.6,
            "unit": "billion",
            "currency": "USD",
            "period": "FY2024",
            "basis": "actual",
            "evidence_quote": "Capital expenditure for FY2024 was US$ 4.6 billion",
            "confidence": 0.95,
        },
        {
            "statement": (
                "Non-revenue water was 18.4% for the fiscal year ended September 30, 2024."
            ),
            "kind": "quantitative",
            "page_number": 2,
            "subject": "Cascadia Water Authority",
            "predicate": "non-revenue water",
            "value_text": "18.4%",
            "value_number": 18.4,
            "unit": "%",
            "period": "fiscal year ended September 30, 2024",
            "basis": "actual",
            "evidence_quote": (
                "Non-revenue water was 18.4% for the fiscal year ended September 30, 2024"
            ),
            "confidence": 0.93,
        },
        {
            "statement": "Treated volume reached 812 megalitres per day for FY2024.",
            "kind": "quantitative",
            "page_number": 2,
            "subject": "Cascadia Water Authority",
            "predicate": "treated volume",
            "value_text": "812 megalitres per day",
            "value_number": 812,
            "unit": "megalitres per day",
            "period": "FY2024",
            "evidence_quote": "Treated volume reached 812 megalitres per day for FY2024",
            "confidence": 0.9,
        },
        {
            "statement": "Capital expenditure for FY2024 is restated as US$ 4.9 billion.",
            "kind": "quantitative",
            "page_number": 3,
            "subject": "Cascadia Water Authority",
            "predicate": "capital expenditure",
            "value_text": "US$ 4.9 billion",
            "value_number": 4.9,
            "unit": "billion",
            "currency": "USD",
            "period": "FY2024",
            "basis": "restated",
            "evidence_quote": "Capital expenditure for FY2024 is restated as US$ 4.9 billion",
            "confidence": 0.94,
        },
    ]
}


@pytest.fixture(scope="class")
async def utility(workspace: Path, settings, database, stub):
    path = build_pdf(workspace / "cascadia.pdf", UTILITY_PAGES)
    await ingest(path, settings, stub, UTILITY_PROFILE, UTILITY_FACTS)
    return path


class TestUnseenDocument:
    def test_it_ingests_at_all(self, utility):
        with session_scope() as session:
            document = session.scalar(select(Document))
            assert document.status == "ready"
            assert session.query(Fact).count() >= 3

    def test_a_foreign_fiscal_year_resolves_to_its_own_dates(self, utility):
        """October to September, not April to March. Nothing in the code assumes India."""
        with session_scope() as session:
            fact = session.scalar(
                select(Fact).where(Fact.predicate_surface.ilike("%capital expenditure%"))
            )
            assert fact.period_start == "2023-10-01"
            assert fact.period_end == "2024-09-30"

    def test_two_ways_of_writing_that_year_agree(self, utility):
        """ "FY2024" and "fiscal year ended September 30, 2024" are the same twelve months."""
        with session_scope() as session:
            water = session.scalar(
                select(Fact).where(Fact.predicate_surface.ilike("%non-revenue water%"))
            )
            capex = session.scalar(
                select(Fact).where(Fact.predicate_surface.ilike("%capital expenditure%"))
            )
            assert (water.period_start, water.period_end) == (capex.period_start, capex.period_end)

    def test_a_currency_and_scale_it_has_never_seen_normalise(self, utility):
        with session_scope() as session:
            fact = session.scalar(
                select(Fact).where(Fact.predicate_surface.ilike("%capital expenditure%"))
            )
            assert fact.currency == "USD"
            assert fact.value_base == pytest.approx(4.6e9)

    def test_a_measure_from_another_domain_enters_the_registry(self, utility):
        """The registry is data, so a unit of water is no harder than a unit of money."""
        with session_scope() as session:
            names = {measure.name.lower() for measure in session.scalars(select(Measure))}
            assert any("non-revenue water" in name for name in names)
            assert any("treated volume" in name for name in names)

    def test_the_restatement_supersedes_the_original(self, utility):
        """The rule is about the stated basis, so it fires here exactly as on a filing."""
        with session_scope() as session:
            relation = session.scalar(
                select(Relation).where(Relation.relation_type == REL_SUPERSEDES)
            )
            assert relation is not None
            assert relation.dimension == "vintage"

    def test_nothing_was_matched_on_the_filename(self, utility):
        """A crude but direct check that no rule keyed off this corpus's names."""
        with session_scope() as session:
            document = session.scalar(select(Document))
            assert document.publisher == "Cascadia Water Authority"
            assert document.fiscal_convention == "us_federal"
