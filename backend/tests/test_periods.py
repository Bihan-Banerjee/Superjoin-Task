from datetime import date

import pytest

from app.core.periods import (
    CALENDAR,
    CALENDAR_YEAR,
    FISCAL_YEAR,
    INDIA,
    OVERLAP_CONTAINS,
    OVERLAP_DISJOINT,
    OVERLAP_IDENTICAL,
    POINT,
    QUARTER,
    RANGE,
    infer_convention,
    parse_period,
    relate,
)


@pytest.mark.parametrize(
    ("label", "start", "end"),
    [
        ("FY24", date(2023, 4, 1), date(2024, 3, 31)),
        ("FY 2023-24", date(2023, 4, 1), date(2024, 3, 31)),
        ("2023-24", date(2023, 4, 1), date(2024, 3, 31)),
        ("2024-25", date(2024, 4, 1), date(2025, 3, 31)),
        ("2024/25", date(2024, 4, 1), date(2025, 3, 31)),
        ("fiscal year 2025", date(2024, 4, 1), date(2025, 3, 31)),
        ("the year ended March 31, 2024", date(2023, 4, 1), date(2024, 3, 31)),
    ],
)
def test_indian_fiscal_years_resolve_to_april_march(label, start, end):
    period = parse_period(label, INDIA)
    assert period.kind == FISCAL_YEAR
    assert (period.start, period.end) == (start, end)


@pytest.mark.parametrize(
    ("label", "start", "end"),
    [
        ("Q4FY24", date(2024, 1, 1), date(2024, 3, 31)),
        ("Q4 FY24", date(2024, 1, 1), date(2024, 3, 31)),
        ("Q1 FY2025", date(2024, 4, 1), date(2024, 6, 30)),
        ("fourth quarter of FY24", date(2024, 1, 1), date(2024, 3, 31)),
        ("three months ended December 31, 2023", date(2023, 10, 1), date(2023, 12, 31)),
    ],
)
def test_quarters(label, start, end):
    period = parse_period(label, INDIA)
    assert period.kind == QUARTER
    assert (period.start, period.end) == (start, end)


def test_calendar_year_is_not_treated_as_fiscal():
    period = parse_period("CY2024", INDIA)
    assert period.kind == CALENDAR_YEAR
    assert (period.start, period.end) == (date(2024, 1, 1), date(2024, 12, 31))


def test_point_in_time():
    period = parse_period("as at March 31, 2024", INDIA)
    assert period.kind == POINT
    assert period.start == period.end == date(2024, 3, 31)


def test_multi_period_span_covers_both_ends():
    period = parse_period("2021-22 to 2024-25", INDIA)
    assert period.kind == RANGE
    assert period.start == date(2021, 4, 1)
    assert period.end == date(2025, 3, 31)


def test_same_year_written_three_ways_is_one_period():
    """The reconciler depends on this: differently written labels must collapse."""
    labels = ["FY24", "2023-24", "the year ended March 31, 2024"]
    keys = {parse_period(label, INDIA).key() for label in labels}
    assert len(keys) == 1


def test_quarter_is_contained_by_its_year_not_equal_to_it():
    year = parse_period("FY24", INDIA)
    quarter = parse_period("Q4 FY24", INDIA)
    assert relate(year, quarter) == OVERLAP_CONTAINS
    assert relate(year, year) == OVERLAP_IDENTICAL
    assert relate(year, parse_period("FY25", INDIA)) == OVERLAP_DISJOINT


def test_convention_changes_what_a_span_means():
    indian = parse_period("2023-24", INDIA)
    calendar = parse_period("2023-24", CALENDAR)
    assert indian.start == date(2023, 4, 1)
    assert calendar.start == date(2023, 1, 1)
    assert calendar.end == date(2024, 12, 31)


def test_unparseable_label_is_reported_as_unresolved_not_guessed():
    period = parse_period("during the period under review", INDIA)
    assert not period.resolved
    assert period.key().startswith("unresolved:")


def test_convention_inferred_from_document_language():
    assert infer_convention("figures in ₹ crore for the year ended March 31, 2024") == INDIA
    assert infer_convention("results for the twelve months to December") == CALENDAR


def test_a_day_is_never_read_as_a_year():
    """The regression that made different years look like the same period.

    "December 31, 2021" was matched by the month-and-year rule, which took the first number
    after the month — the day — and expanded 31 into 2031. Every label of that shape landed
    on the same invented month, so figures from different years compared as though they
    covered identical periods and were reported as contradicting each other.
    """
    parsed = parse_period("December 31, 2021", INDIA)
    assert parsed.kind == POINT
    assert parsed.start == date(2021, 12, 31)


def test_period_is_optional_filler_between_the_span_and_ended():
    """Filings write it both ways and mean the same nine months by either."""
    with_filler = parse_period("nine months period ended December 31, 2020", INDIA)
    without = parse_period("nine months ended December 31, 2020", INDIA)

    assert with_filler.start == without.start == date(2020, 4, 1)
    assert with_filler.end == without.end == date(2020, 12, 31)


def test_a_hyphenated_span_reads_the_same_as_a_spaced_one():
    parsed = parse_period("nine-month period ended December 31, 2020", INDIA)
    assert parsed.start == date(2020, 4, 1)
    assert parsed.end == date(2020, 12, 31)


def test_twelve_months_period_ended_is_a_fiscal_year():
    parsed = parse_period("twelve months period ended March 31, 2024", INDIA)
    assert parsed.kind == FISCAL_YEAR
    assert (parsed.start, parsed.end) == (date(2023, 4, 1), date(2024, 3, 31))


def test_the_same_nine_months_of_different_years_do_not_overlap():
    """The pair that was being reported as a contradiction in the ingested corpus."""
    earlier = parse_period("nine months period ended December 31, 2020", INDIA)
    later = parse_period("nine months period ended December 31, 2021", INDIA)
    assert relate(earlier, later) == OVERLAP_DISJOINT


def test_a_month_and_year_without_a_day_still_reads_as_that_month():
    for label, expected in (("December 2021", date(2021, 12, 1)), ("Dec 24", date(2024, 12, 1))):
        parsed = parse_period(label, INDIA)
        assert parsed.start == expected, label
