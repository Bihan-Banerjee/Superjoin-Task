from datetime import date

import pytest

from app.core.units import COUNT, CURRENCY, DURATION, RATIO, RATIO_CHANGE
from app.pipeline.normalize import (
    DocumentContext,
    PageContext,
    document_context_from_profile,
    normalize_candidate,
)

INDIAN_FILING = DocumentContext(
    fiscal_convention="india", default_currency="INR", default_scale=1e6
)


def normalise(**overrides):
    candidate = {
        "statement": "Revenue from services was Rs. 8,142 Cr in FY24",
        "kind": "quantitative",
        "subject": "Delhivery Limited",
        "predicate": "Revenue From Services",
        "value_text": "8,142 Cr",
        "value_number": 8142,
        "unit": "Cr",
        "currency": "INR",
        "period": "FY24",
        "evidence_quote": "Revenue from services grew to Rs. 8,142 Cr in FY24",
        "confidence": 0.9,
    }
    page = overrides.pop("page", PageContext())
    document = overrides.pop("document", INDIAN_FILING)
    candidate.update(overrides)
    return normalize_candidate(candidate, candidate["evidence_quote"], document, page)


def test_scale_written_in_the_unit_is_applied_once():
    """Regression: absorbing the scale twice turned 8,142 Cr into 8.142e17."""
    assert normalise().value_base == pytest.approx(8142 * 1e7)


def test_scale_written_inside_the_value_is_recovered():
    assert normalise(unit="").value_base == pytest.approx(8142 * 1e7)


def test_page_declaration_supplies_a_missing_scale():
    fact = normalise(
        value_text="81,419.7",
        value_number=81419.7,
        unit="",
        currency="",
        page=PageContext(unit_currency="INR", unit_scale=1e6),
    )
    assert fact.unit.unit_class == CURRENCY
    assert fact.value_base == pytest.approx(81419.7 * 1e6)


def test_a_unit_beside_the_number_overrides_the_page_declaration():
    fact = normalise(
        value_text="81,419.7 million",
        value_number=81419.7,
        unit="million",
        page=PageContext(unit_currency="INR", unit_scale=1e7),
    )
    assert fact.value_base == pytest.approx(81419.7 * 1e6)


def test_the_two_delhivery_statements_reduce_to_the_same_amount():
    deck = normalise()
    statements = normalise(
        value_text="81,419.7",
        value_number=81419.7,
        unit="",
        currency="",
        page=PageContext(unit_currency="INR", unit_scale=1e6),
    )
    assert deck.value_base == pytest.approx(statements.value_base, rel=1e-4)


@pytest.mark.parametrize(
    ("unit", "value", "unit_class", "base"),
    [
        ("%", 13, RATIO, 13.0),
        ("bps", 50, RATIO_CHANGE, 0.5),
        ("Mn shipments", 740, COUNT, 740e6),
        ("days", 45, DURATION, 45.0),
    ],
)
def test_a_declared_currency_scale_is_not_applied_to_other_kinds_of_quantity(
    unit, value, unit_class, base
):
    """Regression: the document's "in millions" was multiplying percentages."""
    fact = normalise(unit=unit, value_text=f"{value}", value_number=value, currency="")
    assert fact.unit.unit_class == unit_class
    assert fact.value_base == pytest.approx(base)


def test_period_resolves_using_the_documents_fiscal_convention():
    fact = normalise(period="2023-24")
    assert fact.period.start == date(2023, 4, 1)
    assert fact.period.end == date(2024, 3, 31)


def test_an_unresolvable_period_is_flagged_rather_than_dropped():
    fact = normalise(period="during the period under review")
    assert "period_unresolved" in fact.issues


def test_basis_is_read_back_off_the_evidence_when_the_extractor_omits_it():
    fact = normalise(
        evidence_quote="Revenue from services (pro forma) was Rs. 8,142 Cr in FY24",
        basis="unspecified",
    )
    assert fact.basis == "pro_forma"


def test_claim_key_ignores_the_value_so_disagreeing_facts_still_pair():
    high = normalise()
    low = normalise(value_text="7,224 Cr", value_number=7224)
    assert high.claim_key() == low.claim_key()


def test_claim_key_separates_facts_that_differ_in_scope():
    total = normalise()
    segment = normalise(qualifiers={"segment": "Express Parcel"})
    assert total.claim_key() != segment.claim_key()


def test_qualifiers_drop_placeholder_values():
    fact = normalise(qualifiers={"segment": "n/a", "geography": "India", "": "x"})
    assert fact.qualifiers == {"geography": "India"}


def test_document_context_is_built_from_a_profile():
    context = document_context_from_profile(
        {
            "fiscal_convention": "india",
            "default_currency": "₹",
            "default_scale": "crore",
            "publisher": "Delhivery Limited",
        }
    )
    assert context.default_currency == "INR"
    assert context.default_scale == 1e7
    assert context.fiscal_convention == "india"


def test_an_unknown_fiscal_convention_falls_back_to_calendar():
    assert document_context_from_profile({"fiscal_convention": "martian"}).fiscal_convention == (
        "calendar"
    )


class TestCountedThings:
    """A measure names what it counts, and that beats the document's currency."""

    def test_shipments_in_millions_are_shipments_not_rupees(self):
        fact = normalise(
            predicate="express parcel shipments",
            unit="million",
            value_text="740",
            value_number=740,
            currency="",
        )
        assert fact.unit_class == COUNT
        assert fact.value_base == pytest.approx(740e6)

    def test_a_bare_count_does_not_pick_up_the_declared_scale(self):
        fact = normalise(
            predicate="gateways", unit="", value_text="111", value_number=111, currency=""
        )
        assert fact.unit_class == COUNT
        assert fact.value_base == pytest.approx(111)

    def test_a_monetary_measure_still_inherits_the_declared_scale(self):
        fact = normalise(
            predicate="revenue from services",
            unit="",
            value_text="8,142",
            value_number=8142,
            currency="",
        )
        assert fact.unit_class == CURRENCY
        assert fact.value_base == pytest.approx(8142 * 1e6)


class TestPeriodInMeasureName:
    """ "FY24 EBITDA margin" and "FY23 EBITDA margin" are one measure at two periods.

    Leaving the year in the name splits the registry so the two can never be compared.
    """

    def test_the_period_is_moved_out_of_the_measure_name(self):
        fact = normalise(predicate="FY23 adj. EBITDA", period="")
        assert fact.predicate == "adj. ebitda"
        assert fact.period_label == "fy23"
        assert fact.period.start == date(2022, 4, 1)

    def test_an_explicit_period_field_wins_over_the_one_in_the_name(self):
        fact = normalise(predicate="FY23 adj. EBITDA", period="FY24")
        assert fact.predicate == "adj. ebitda"
        assert fact.period_label == "FY24"

    def test_two_years_of_one_measure_share_a_claim_key_shape(self):
        first = normalise(predicate="FY23 EBITDA margin", period="")
        second = normalise(predicate="FY24 EBITDA margin", period="")
        assert first.predicate == second.predicate == "ebitda margin"

    def test_a_measure_with_no_period_is_untouched(self):
        assert normalise(predicate="revenue from services").predicate == "revenue from services"

    def test_a_name_that_is_only_a_period_is_left_alone(self):
        assert normalise(predicate="FY24").predicate == "fy24"


def test_a_count_does_not_inherit_the_documents_currency():
    """The filing declares rupees in million; pin codes are not rupees.

    "18,793 pin codes covered" was coming out of the corpus as 1.879e13 INR billion: a
    figure no document contains, which then competed with real money in comparisons.
    """
    normalised = normalise(
        statement="Delhivery covered 18,793 pin codes as of March 31, 2024.",
        predicate="pin codes covered",
        value_text="18,793",
        value_number=18793,
        unit="",
        currency="",
        period="as at March 31, 2024",
        evidence_quote="18,793 Pin codes covered",
    )
    assert normalised.currency is None
    assert normalised.value_base == 18793


def test_an_amount_with_no_unit_still_inherits_the_declaration():
    """The behaviour the gate must not break: the headline reconciliation needs it."""
    normalised = normalise(
        statement="Revenue from services for FY24 was 81,419.7",
        predicate="revenue from services",
        value_text="81,419.7",
        value_number=81419.7,
        unit="",
        currency="",
        evidence_quote="Revenue from services 81,419.7",
    )
    assert normalised.currency == "INR"
    assert normalised.value_base == pytest.approx(81419.7 * 1e6)


def test_a_date_is_not_scanned_for_a_quantity():
    """ "May 10, 2022" is not the number 10.

    157 of the corpus's 158 temporal facts carried a spurious figure pulled out of the date,
    which then entered numeric comparison: two unrelated dates falling on the 14th look like
    agreement to a reconciler working on the normalised value.
    """
    normalised = normalise(
        statement="Suvir Suren Sujan has been a Director since March 7, 2019.",
        kind="temporal",
        predicate="period of directorship",
        value_text="Since March 7, 2019",
        value_number=None,
        unit="",
        currency="",
        period="",
        evidence_quote="Period of Directorship: Since March 7, 2019",
    )
    assert normalised.value_number is None
    assert normalised.value_base is None
    assert normalised.unit is None


def test_a_quantity_is_still_read_as_one():
    """The guard must not swallow genuine measurements."""
    assert normalise().value_base == pytest.approx(8142 * 1e7)


def test_a_per_share_amount_does_not_inherit_the_documents_scale():
    """A face value of ₹1 each is one rupee, not one million rupees.

    "All amounts in Indian Rupees in million" is a statement about the aggregates in a
    statement, not about a per-share figure printed beside them. Getting this wrong is an
    error of six orders of magnitude on exactly the numbers a reader checks first.
    """
    for predicate in ("face value", "average cost of acquisition per equity share"):
        normalised = normalise(
            statement=f"The {predicate} is Rs 1 each.",
            predicate=predicate,
            value_text="₹1",
            value_number=1,
            unit="INR",
            currency="INR",
            period="",
            evidence_quote="face value of the Equity Shares is ₹1 each",
        )
        assert normalised.currency == "INR", predicate
        assert normalised.value_base == 1, predicate


def test_an_aggregate_still_inherits_the_scale():
    """The guard must not reach figures the declaration really is about."""
    normalised = normalise(
        statement="Revenue from services for FY24 was 81,415",
        predicate="revenue from services",
        value_text="81,415",
        value_number=81415,
        unit="",
        currency="INR",
        evidence_quote="Revenue from services 81,415",
    )
    assert normalised.value_base == pytest.approx(81415 * 1e6)
