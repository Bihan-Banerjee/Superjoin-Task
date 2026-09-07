"""Rule engine behaviour, expressed as the scenarios it exists to tell apart."""

from app.db.models import (
    DIM_BASIS,
    DIM_CURRENCY,
    DIM_PERIOD,
    DIM_SEGMENT,
    DIM_UNIT_SCALE,
    DIM_VINTAGE,
    REL_CONTRADICTS,
    REL_CORROBORATES,
    REL_RECONCILED,
    REL_REFINES,
    Fact,
)
from app.pipeline.reconcile import reconcile

CRORE = 1e7
MILLION = 1e6


def fact(**overrides) -> Fact:
    defaults = {
        "document_id": 1,
        "page_id": 1,
        "measure_id": 10,
        "entity_id": 5,
        "predicate_surface": "revenue from services",
        "value_base": 8142 * CRORE,
        "value_text": "8,142 Cr",
        "unit_class": "currency",
        "currency": "INR",
        "unit_scale": CRORE,
        "unit_canonical": "INR crore",
        "period_label": "FY24",
        "period_kind": "fiscal_year",
        "period_start": "2023-04-01",
        "period_end": "2024-03-31",
        "qualifiers": {},
        "basis": None,
        "confidence": 0.9,
        "evidence_score": 100.0,
    }
    defaults.update(overrides)
    return Fact(**defaults)


def other(**overrides) -> Fact:
    return fact(document_id=2, **overrides)


def test_the_same_amount_in_crore_and_millions_corroborates():
    """The starter corpus states this figure both ways; it must not read as a conflict."""
    verdict = reconcile(
        fact(),
        other(value_base=81419.7 * MILLION, value_text="81,419.7", unit_scale=MILLION),
    )
    assert verdict.relation_type == REL_CORROBORATES
    assert verdict.dimension == DIM_UNIT_SCALE


def test_identical_figures_corroborate_without_naming_a_dimension():
    verdict = reconcile(fact(), other())
    assert verdict.relation_type == REL_CORROBORATES
    assert verdict.subtype == "exact"


def test_a_quarter_inside_a_year_refines_rather_than_contradicts():
    verdict = reconcile(
        fact(),
        other(
            value_base=2076 * CRORE,
            value_text="2,076 Cr",
            period_label="Q4 FY24",
            period_kind="quarter",
            period_start="2024-01-01",
            period_end="2024-03-31",
        ),
    )
    assert verdict.relation_type == REL_REFINES
    assert verdict.dimension == DIM_PERIOD


def test_different_years_are_reconciled_by_period():
    verdict = reconcile(
        fact(),
        other(
            value_base=7224 * CRORE,
            value_text="7,224 Cr",
            period_label="FY23",
            period_start="2022-04-01",
            period_end="2023-03-31",
        ),
    )
    assert verdict.relation_type == REL_RECONCILED
    assert verdict.dimension == DIM_PERIOD


def test_a_segment_figure_against_a_total_is_reconciled_by_scope():
    verdict = reconcile(
        fact(),
        other(
            value_base=5077 * CRORE,
            value_text="5,077 Cr",
            qualifiers={"segment": "Express Parcel"},
        ),
    )
    assert verdict.relation_type == REL_RECONCILED
    assert verdict.dimension == DIM_SEGMENT


def test_an_estimate_and_an_outcome_are_reconciled_by_vintage():
    verdict = reconcile(
        fact(
            value_base=6.5, value_text="6.5%", unit_class="ratio", currency=None, basis="estimate"
        ),
        other(value_base=8.2, value_text="8.2%", unit_class="ratio", currency=None, basis="actual"),
    )
    assert verdict.relation_type == REL_RECONCILED
    assert verdict.dimension == DIM_VINTAGE


def test_agreeing_figures_on_different_bases_still_report_the_difference():
    verdict = reconcile(
        fact(
            value_base=6.5, value_text="6.5%", unit_class="ratio", currency=None, basis="projection"
        ),
        other(value_base=6.5, value_text="6.5%", unit_class="ratio", currency=None, basis="actual"),
    )
    assert verdict.relation_type == REL_CORROBORATES
    assert verdict.dimension in {DIM_BASIS, DIM_VINTAGE}
    assert "different bases" in verdict.explanation


def test_a_real_disagreement_is_reported_as_a_contradiction():
    verdict = reconcile(fact(), other(value_base=8500 * CRORE, value_text="8,500 Cr"))
    assert verdict.relation_type == REL_CONTRADICTS
    assert verdict.severity > 0
    assert "8,500 Cr" in verdict.explanation


def test_percentages_a_tenth_apart_are_not_treated_as_the_same_figure():
    """6.5% and 6.6% growth are different published numbers."""
    verdict = reconcile(
        fact(value_base=6.5, value_text="6.5%", unit_class="ratio", currency=None),
        other(value_base=6.6, value_text="6.6%", unit_class="ratio", currency=None),
    )
    assert verdict.relation_type == REL_CONTRADICTS


def test_a_rounded_restatement_of_the_same_percentage_still_corroborates():
    verdict = reconcile(
        fact(value_base=4.9, value_text="4.9%", unit_class="ratio", currency=None),
        other(value_base=4.94, value_text="4.94%", unit_class="ratio", currency=None),
    )
    assert verdict.relation_type == REL_CORROBORATES


def test_different_currencies_are_never_converted():
    verdict = reconcile(
        fact(), other(value_base=980 * MILLION, value_text="980 million", currency="USD")
    )
    assert verdict.relation_type == REL_RECONCILED
    assert verdict.dimension == DIM_CURRENCY
    assert "exchange rate" in verdict.explanation


def test_an_implausibly_large_gap_goes_to_the_model_rather_than_being_asserted():
    verdict = reconcile(fact(), other(value_base=200 * CRORE, value_text="200 Cr"))
    assert verdict.escalate


def test_an_unresolved_period_goes_to_the_model():
    verdict = reconcile(
        fact(),
        other(
            value_base=7000 * CRORE,
            value_text="7,000 Cr",
            period_start=None,
            period_end=None,
            period_label="the period under review",
        ),
    )
    assert verdict.escalate


def test_low_extraction_confidence_goes_to_the_model():
    verdict = reconcile(
        fact(confidence=0.5), other(value_base=8500 * CRORE, value_text="8,500 Cr", confidence=0.5)
    )
    assert verdict.escalate


def test_facts_about_different_measures_are_dropped_not_compared():
    assert reconcile(fact(), other(measure_id=11)).rule_id == "not-comparable"


def test_facts_about_different_entities_are_dropped():
    assert reconcile(fact(), other(entity_id=6)).rule_id == "not-comparable"


def test_textual_facts_are_escalated_because_arithmetic_cannot_decide_them():
    verdict = reconcile(
        fact(value_base=None, value_text="Gurugram, Haryana", unit_class=None, currency=None),
        other(value_base=None, value_text="Gurgaon, Haryana", unit_class=None, currency=None),
    )
    assert verdict.escalate


def test_contradiction_severity_rises_with_the_size_of_the_gap():
    small = reconcile(fact(), other(value_base=8300 * CRORE, value_text="8,300 Cr"))
    large = reconcile(fact(), other(value_base=6000 * CRORE, value_text="6,000 Cr"))
    assert large.severity > small.severity
