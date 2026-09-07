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
