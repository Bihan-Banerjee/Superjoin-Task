from app.pipeline.ground import (
    DUPLICATE,
    QUOTE_NOT_FOUND,
    SUBJECT_UNRESOLVED,
    VALUE_ABSENT_FROM_QUOTE,
    ground_candidates,
    unattributed_rejections,
)

PAGE = (
    "Revenue from services grew 13% to Rs. 8,142 Cr in FY24 from 7,224 Cr in FY23. "
    "The Board noted that margins improved during the year."
)


def candidate(**overrides):
    base = {
        "statement": "Revenue from services was Rs. 8,142 Cr in FY24",
        "kind": "quantitative",
        "subject": "Delhivery Limited",
        "predicate": "revenue from services",
        "value_text": "8,142 Cr",
        "value_number": 8142,
        "unit": "Cr",
        "currency": "INR",
        "period": "FY24",
        "evidence_quote": "Revenue from services grew 13% to Rs. 8,142 Cr in FY24",
        "confidence": 0.9,
    }
    base.update(overrides)
    return base


def test_a_well_formed_fact_is_grounded_with_a_span_into_the_page():
    outcome = ground_candidates([candidate()], PAGE)
    assert len(outcome.grounded) == 1
    fact = outcome.grounded[0]
    assert PAGE[fact.start : fact.end] == fact.quote
    assert "8,142" in fact.quote


def test_a_hallucinated_quote_is_rejected():
    invented = candidate(
        value_text="9,999 Cr",
        value_number=9999,
        evidence_quote="Revenue from services reached 9,999 Cr in FY24",
    )
    outcome = ground_candidates([invented], PAGE)
    assert not outcome.grounded
    assert outcome.rejected[0].reason == QUOTE_NOT_FOUND


def test_a_real_quote_carrying_the_wrong_value_is_rejected():
    """The dangerous failure: plausible sentence, unrelated number."""
    mismatched = candidate(
        value_text="5,000 Cr",
        value_number=5000,
        evidence_quote="The Board noted that margins improved during the year.",
    )
    outcome = ground_candidates([mismatched], PAGE)
    assert outcome.rejected[0].reason == VALUE_ABSENT_FROM_QUOTE


def test_value_matching_tolerates_formatting_differences():
    reformatted = candidate(value_text="8142", value_number=8142)
    assert ground_candidates([reformatted], PAGE).grounded


def test_a_fact_without_an_identifiable_subject_is_rejected():
    vague = candidate(subject="the company")
    assert ground_candidates([vague], PAGE).rejected[0].reason == SUBJECT_UNRESOLVED


def test_the_same_fact_extracted_twice_is_kept_once():
    outcome = ground_candidates([candidate(), candidate()], PAGE)
    assert len(outcome.grounded) == 1
    assert outcome.rejected[0].reason == DUPLICATE


def test_two_different_facts_from_one_sentence_both_survive():
    other = candidate(
        statement="Revenue from services was Rs. 7,224 Cr in FY23",
        value_text="7,224 Cr",
        value_number=7224,
        period="FY23",
        evidence_quote="8,142 Cr in FY24 from 7,224 Cr in FY23",
    )
    outcome = ground_candidates([candidate(), other], PAGE)
    assert len(outcome.grounded) == 2


def test_pass_rate_reflects_what_was_discarded():
    outcome = ground_candidates(
        [candidate(), candidate(subject="it"), candidate(subject="they")], PAGE
    )
    assert outcome.pass_rate == 1 / 3


def test_model_reported_unattributed_values_become_rejections():
    rejections = unattributed_rejections(
        [{"value_text": "62%", "reason": "segment share, legend order ambiguous"}]
    )
    assert len(rejections) == 1
    assert "legend" in rejections[0].detail
