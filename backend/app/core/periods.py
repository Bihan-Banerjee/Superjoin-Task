"""Reporting periods.

The single largest source of apparent contradictions in this corpus is period labelling.
The same twelve months appear as "FY24", "FY 2023-24", "2023-24", "2023/24" and "the year
ended March 31, 2024", while "Q4 FY24" and "FY24" are different periods that share a name
fragment and a subject.

Every period is therefore resolved to a half-open interval of real dates plus a kind. Two
facts share a period only if their intervals are identical; everything else is classified
by how the intervals relate (containment, overlap, disjoint), which is what lets the
reconciler say *why* two numbers differ instead of just that they do.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date

INDIA = "india"
CALENDAR = "calendar"
US_FEDERAL = "us_federal"
UK = "uk"

# Month the fiscal year starts in, per convention.
FISCAL_START_MONTH: dict[str, int] = {
    INDIA: 4,
    CALENDAR: 1,
    US_FEDERAL: 10,
    UK: 4,
}

FISCAL_YEAR = "fiscal_year"
CALENDAR_YEAR = "calendar_year"
QUARTER = "quarter"
HALF = "half"
MONTH = "month"
POINT = "point"
RANGE = "range"
UNKNOWN = "unknown"

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

_ORDINALS = {
    "first": 1,
    "1st": 1,
    "one": 1,
    "second": 2,
    "2nd": 2,
    "two": 2,
    "third": 3,
    "3rd": 3,
    "three": 3,
    "fourth": 4,
    "4th": 4,
    "four": 4,
}


@dataclass(frozen=True)
class Period:
    """A resolved reporting period."""

    label: str
    kind: str
    start: date | None
    end: date | None
    convention: str = INDIA

    @property
    def resolved(self) -> bool:
        return self.start is not None and self.end is not None

    @property
    def days(self) -> int:
        if not self.resolved:
            return 0
        return (self.end - self.start).days + 1

    def key(self) -> str:
        if not self.resolved:
            return f"unresolved:{self.label.strip().lower()}"
        return f"{self.start.isoformat()}/{self.end.isoformat()}"

    def contains(self, other: Period) -> bool:
        if not (self.resolved and other.resolved):
            return False
        return self.start <= other.start and other.end <= self.end

    def overlaps(self, other: Period) -> bool:
        if not (self.resolved and other.resolved):
            return False
        return self.start <= other.end and other.start <= self.end


UNRESOLVED = Period(label="", kind=UNKNOWN, start=None, end=None)


def _end_of_month(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _shift_months(year: int, month: int, count: int) -> tuple[int, int]:
    index = (year * 12 + (month - 1)) + count
    return index // 12, index % 12 + 1


def fiscal_year_bounds(end_year: int, convention: str = INDIA) -> tuple[date, date]:
    """Interval for the fiscal year *labelled* by `end_year`.

    Indian filings name a fiscal year after the calendar year it ends in: FY24 runs from
    1 April 2023 to 31 March 2024. A calendar convention collapses to the year itself.
    """
    start_month = FISCAL_START_MONTH.get(convention, 4)
    if start_month == 1:
        return date(end_year, 1, 1), date(end_year, 12, 31)
    start = date(end_year - 1, start_month, 1)
    end_year_month, end_month = _shift_months(start.year, start.month, 11)
    return start, _end_of_month(end_year_month, end_month)


def fiscal_quarter_bounds(
    end_year: int, quarter: int, convention: str = INDIA
) -> tuple[date, date]:
    start, _ = fiscal_year_bounds(end_year, convention)
    q_year, q_month = _shift_months(start.year, start.month, 3 * (quarter - 1))
    q_end_year, q_end_month = _shift_months(q_year, q_month, 2)
    return date(q_year, q_month, 1), _end_of_month(q_end_year, q_end_month)


def fiscal_half_bounds(end_year: int, half: int, convention: str = INDIA) -> tuple[date, date]:
    start, _ = fiscal_year_bounds(end_year, convention)
    h_year, h_month = _shift_months(start.year, start.month, 6 * (half - 1))
    h_end_year, h_end_month = _shift_months(h_year, h_month, 5)
    return date(h_year, h_month, 1), _end_of_month(h_end_year, h_end_month)


def _expand_two_digit_year(value: int) -> int:
    if value >= 100:
        return value
    # Filings in this domain span roughly 1990-2090; 70 is a conventional pivot.
    return 2000 + value if value < 70 else 1900 + value


def _span_end_year(first: int, second: int) -> int:
    """End year for a span written as 2023-24, 2023/2024, 23-24 etc."""
    first = _expand_two_digit_year(first)
    if second >= 100:
        return second
    candidate = (first // 100) * 100 + second
    if candidate <= first:
        candidate += 100
    return candidate


_QUARTER_PATTERNS = [
    re.compile(
        r"\bq\s*(?P<q>[1-4])\s*[-/ ]?\s*(?:fy|f\.y\.?|fiscal)?\s*"
        r"(?P<y1>\d{2,4})(?:\s*[-/]\s*(?P<y2>\d{2,4}))?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:fy|f\.y\.?|fiscal)\s*(?P<y1>\d{2,4})(?:\s*[-/]\s*(?P<y2>\d{2,4}))?\s*q\s*(?P<q>[1-4])\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?P<word>first|second|third|fourth|1st|2nd|3rd|4th)\s+quarter\s+"
        r"(?:of\s+)?(?:the\s+)?(?:fy|fiscal(?:\s+year)?|financial\s+year)?\s*"
        r"(?P<y1>\d{2,4})(?:\s*[-/]\s*(?P<y2>\d{2,4}))?\b",
        re.IGNORECASE,
    ),
]

_HALF_PATTERN = re.compile(
    r"\bh\s*(?P<h>[12])\s*[-/ ]?\s*(?:fy|f\.y\.?|fiscal)?\s*"
    r"(?P<y1>\d{2,4})(?:\s*[-/]\s*(?P<y2>\d{2,4}))?\b",
    re.IGNORECASE,
)

_FISCAL_SPAN_PATTERN = re.compile(
    r"\b(?:fy|f\.y\.?|fiscal(?:\s+year)?|financial\s+year)\s*[:\-]?\s*"
    r"(?P<y1>\d{4}|\d{2})\s*[-/]\s*(?P<y2>\d{4}|\d{2})\b",
    re.IGNORECASE,
)

_FISCAL_SINGLE_PATTERN = re.compile(
    r"\b(?:fy|f\.y\.?|fiscal(?:\s+year)?|financial\s+year)\s*[:\-]?\s*(?P<y1>\d{4}|\d{2})\b",
    re.IGNORECASE,
)

_BARE_SPAN_PATTERN = re.compile(r"\b(?P<y1>(?:19|20)\d{2})\s*[-/]\s*(?P<y2>\d{2}|\d{4})\b")

_YEAR_ENDED_PATTERN = re.compile(
    r"\b(?:year|twelve\s+months|12\s+months)\s+ended\s+(?:on\s+)?"
    r"(?P<rest>[A-Za-z0-9,\s]{6,30})",
    re.IGNORECASE,
)

_PERIOD_ENDED_PATTERN = re.compile(
    r"\b(?P<count>three|six|nine|3|6|9)\s+months\s+ended\s+(?:on\s+)?(?P<rest>[A-Za-z0-9,\s]{6,30})",
    re.IGNORECASE,
)

_AS_AT_PATTERN = re.compile(
    r"\b(?:as\s+(?:at|of|on)|as\s+at\s+the\s+end\s+of)\s+(?P<rest>[A-Za-z0-9,\s]{6,30})",
    re.IGNORECASE,
)

_DATE_TEXT_PATTERN = re.compile(
    r"\b(?P<d1>\d{1,2})?\s*(?P<month>[A-Za-z]{3,9})\.?\s*(?P<d2>\d{1,2})?,?\s*(?P<year>(?:19|20)\d{2})\b"
)

_ISO_DATE_PATTERN = re.compile(r"\b(?P<y>(?:19|20)\d{2})-(?P<m>\d{2})-(?P<d>\d{2})\b")

_CALENDAR_YEAR_PATTERN = re.compile(
    r"\b(?:cy|calendar\s+year)\s*[:\-]?\s*(?P<y>(?:19|20)?\d{2})\b", re.IGNORECASE
)

_BARE_YEAR_PATTERN = re.compile(r"\b(?P<y>(?:19|20)\d{2})\b")

_MONTH_YEAR_PATTERN = re.compile(
    r"\b(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?"
    r"|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s*[-,\s]\s*(?P<y>(?:19|20)?\d{2})\b",
    re.IGNORECASE,
)


def parse_period(label: str | None, convention: str = INDIA) -> Period:
    """Resolve a period label to a dated interval.

    Order matters: the most specific patterns run first so that "Q4 FY24" is not eaten by
    the fiscal-year matcher, and "nine months ended December 31, 2023" is not reduced to a
    point in time by the date matcher.
    """
    if not label:
        return UNRESOLVED
    text = " ".join(str(label).split())
    if not text:
        return UNRESOLVED

    for parser in (
        _try_explicit_range,
        _try_quarter,
        _try_half,
        _try_period_ended,
        _try_year_ended,
        _try_fiscal_span,
        _try_fiscal_single,
        _try_bare_span,
        _try_calendar_year,
        _try_as_at,
        _try_iso_date,
        _try_month_year,
        _try_explicit_date,
        _try_bare_year,
    ):
        period = parser(text, convention)
        if period is not None:
            return period
    return Period(label=text, kind=UNKNOWN, start=None, end=None, convention=convention)


_RANGE_SEPARATOR = re.compile(r"\s+(?:to|through|until|till|[-–—])\s+", re.IGNORECASE)


def _try_explicit_range(text: str, convention: str) -> Period | None:
    """Handle "2021-22 to 2024-25" and "FY22 through FY25" as one spanning interval.

    Without this the first sub-period wins and the fact is silently mis-scoped, which is
    worse than leaving it unresolved because the error is invisible downstream.
    """
    parts = _RANGE_SEPARATOR.split(text, maxsplit=1)
    if len(parts) != 2:
        return None
    left_text, right_text = (part.strip() for part in parts)
    if not left_text or not right_text:
        return None
    # Both sides must independently look like periods, otherwise this is ordinary prose.
    left = parse_period(left_text, convention)
    right = parse_period(right_text, convention)
    if not (left.resolved and right.resolved):
        return None
    if left.kind == UNKNOWN or right.kind == UNKNOWN:
        return None
    start, end = min(left.start, right.start), max(left.end, right.end)
    if start == left.start and end == left.end:
        return None
    return Period(label=text, kind=RANGE, start=start, end=end, convention=convention)


def _resolve_span_years(y1_raw: str, y2_raw: str | None) -> int:
    first = int(y1_raw)
    if y2_raw is None:
        return _expand_two_digit_year(first)
    return _span_end_year(first, int(y2_raw))


def _try_quarter(text: str, convention: str) -> Period | None:
    for pattern in _QUARTER_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        groups = match.groupdict()
        quarter = (
            int(groups["q"]) if groups.get("q") else _ORDINALS.get(groups.get("word", "").lower())
        )
        if not quarter:
            continue
        end_year = _resolve_span_years(groups["y1"], groups.get("y2"))
        start, end = fiscal_quarter_bounds(end_year, quarter, convention)
        return Period(label=text, kind=QUARTER, start=start, end=end, convention=convention)
    return None


def _try_half(text: str, convention: str) -> Period | None:
    match = _HALF_PATTERN.search(text)
    if not match:
        return None
    end_year = _resolve_span_years(match.group("y1"), match.group("y2"))
    start, end = fiscal_half_bounds(end_year, int(match.group("h")), convention)
    return Period(label=text, kind=HALF, start=start, end=end, convention=convention)


def _try_fiscal_span(text: str, convention: str) -> Period | None:
    match = _FISCAL_SPAN_PATTERN.search(text)
    if not match:
        return None
    end_year = _span_end_year(int(match.group("y1")), int(match.group("y2")))
    start, end = fiscal_year_bounds(end_year, convention)
    return Period(label=text, kind=FISCAL_YEAR, start=start, end=end, convention=convention)


def _try_fiscal_single(text: str, convention: str) -> Period | None:
    match = _FISCAL_SINGLE_PATTERN.search(text)
    if not match:
        return None
    end_year = _expand_two_digit_year(int(match.group("y1")))
    start, end = fiscal_year_bounds(end_year, convention)
    return Period(label=text, kind=FISCAL_YEAR, start=start, end=end, convention=convention)


def _try_bare_span(text: str, convention: str) -> Period | None:
    """Handle "2024-25" and "2024/25" with no FY prefix.

    Institutional publishers write Indian fiscal years this way constantly. It is only
    treated as fiscal when the convention says so; under a calendar convention the same
    string is a multi-year range.
    """
    match = _BARE_SPAN_PATTERN.search(text)
    if not match:
        return None
    first = int(match.group("y1"))
    second = int(match.group("y2"))
    end_year = _span_end_year(first, second)
    if end_year - first > 1:
        return Period(
            label=text,
            kind=RANGE,
            start=date(first, 1, 1),
            end=date(end_year, 12, 31),
            convention=convention,
        )
    if convention == CALENDAR:
        return Period(
            label=text,
            kind=RANGE,
            start=date(first, 1, 1),
            end=date(end_year, 12, 31),
            convention=convention,
        )
    start, end = fiscal_year_bounds(end_year, convention)
    return Period(label=text, kind=FISCAL_YEAR, start=start, end=end, convention=convention)


def _try_calendar_year(text: str, convention: str) -> Period | None:
    match = _CALENDAR_YEAR_PATTERN.search(text)
    if not match:
        return None
    year = _expand_two_digit_year(int(match.group("y")))
    return Period(
        label=text,
        kind=CALENDAR_YEAR,
        start=date(year, 1, 1),
        end=date(year, 12, 31),
        convention=CALENDAR,
    )


def _try_year_ended(text: str, convention: str) -> Period | None:
    match = _YEAR_ENDED_PATTERN.search(text)
    if not match:
        return None
    end = _read_date(match.group("rest"))
    if end is None:
        return None
    start_year, start_month = _shift_months(end.year, end.month, -11)
    return Period(
        label=text,
        kind=FISCAL_YEAR,
        start=date(start_year, start_month, 1),
        end=end,
        convention=convention,
    )


def _try_period_ended(text: str, convention: str) -> Period | None:
    match = _PERIOD_ENDED_PATTERN.search(text)
    if not match:
        return None
    counts = {"three": 3, "3": 3, "six": 6, "6": 6, "nine": 9, "9": 9}
    months = counts.get(match.group("count").lower())
    end = _read_date(match.group("rest"))
    if not months or end is None:
        return None
    start_year, start_month = _shift_months(end.year, end.month, -(months - 1))
    kind = QUARTER if months == 3 else HALF if months == 6 else RANGE
    return Period(
        label=text,
        kind=kind,
        start=date(start_year, start_month, 1),
        end=end,
        convention=convention,
    )


def _try_as_at(text: str, convention: str) -> Period | None:
    match = _AS_AT_PATTERN.search(text)
    if not match:
        return None
    moment = _read_date(match.group("rest"))
    if moment is None:
        return None
    return Period(label=text, kind=POINT, start=moment, end=moment, convention=convention)


def _try_iso_date(text: str, convention: str) -> Period | None:
    match = _ISO_DATE_PATTERN.search(text)
    if not match:
        return None
    try:
        moment = date(int(match.group("y")), int(match.group("m")), int(match.group("d")))
    except ValueError:
        return None
    return Period(label=text, kind=POINT, start=moment, end=moment, convention=convention)


def _try_month_year(text: str, convention: str) -> Period | None:
    match = _MONTH_YEAR_PATTERN.search(text)
    if not match:
        return None
    month = _MONTHS.get(match.group("month").lower().rstrip("."))
    if not month:
        return None
    year = _expand_two_digit_year(int(match.group("y")))
    return Period(
        label=text,
        kind=MONTH,
        start=date(year, month, 1),
        end=_end_of_month(year, month),
        convention=convention,
    )


def _try_explicit_date(text: str, convention: str) -> Period | None:
    moment = _read_date(text)
    if moment is None:
        return None
    return Period(label=text, kind=POINT, start=moment, end=moment, convention=convention)


def _try_bare_year(text: str, convention: str) -> Period | None:
    match = _BARE_YEAR_PATTERN.search(text)
    if not match:
        return None
    year = int(match.group("y"))
    return Period(
        label=text,
        kind=CALENDAR_YEAR,
        start=date(year, 1, 1),
        end=date(year, 12, 31),
        convention=CALENDAR,
    )


def _read_date(text: str) -> date | None:
    """Read "March 31, 2024", "31 March 2024" or "31.03.2024" out of a fragment."""
    if not text:
        return None
    iso = _ISO_DATE_PATTERN.search(text)
    if iso:
        try:
            return date(int(iso.group("y")), int(iso.group("m")), int(iso.group("d")))
        except ValueError:
            return None

    match = _DATE_TEXT_PATTERN.search(text)
    if not match:
        return None
    month = _MONTHS.get(match.group("month").lower().rstrip("."))
    if not month:
        return None
    year = int(match.group("year"))
    day_text = match.group("d2") or match.group("d1")
    if day_text:
        try:
            return date(year, month, int(day_text))
        except ValueError:
            return _end_of_month(year, month)
    return _end_of_month(year, month)


OVERLAP_IDENTICAL = "identical"
OVERLAP_CONTAINS = "contains"
OVERLAP_CONTAINED = "contained_by"
OVERLAP_PARTIAL = "partial"
OVERLAP_DISJOINT = "disjoint"
OVERLAP_UNKNOWN = "unknown"


def relate(left: Period, right: Period) -> str:
    if not (left.resolved and right.resolved):
        return OVERLAP_UNKNOWN
    if left.start == right.start and left.end == right.end:
        return OVERLAP_IDENTICAL
    if left.contains(right):
        return OVERLAP_CONTAINS
    if right.contains(left):
        return OVERLAP_CONTAINED
    if left.overlaps(right):
        return OVERLAP_PARTIAL
    return OVERLAP_DISJOINT


def describe(period: Period) -> str:
    if not period.resolved:
        return period.label or "unspecified period"
    if period.kind == POINT:
        return period.start.isoformat()
    return f"{period.start.isoformat()} to {period.end.isoformat()}"


def infer_convention(text: str | None) -> str:
    """Guess a document's fiscal convention from its own language.

    Indian filings say so explicitly and often; anything else falls back to the calendar
    year, which is the safer default because it never silently shifts a period by a quarter.
    """
    if not text:
        return CALENDAR
    lowered = text.lower()
    indian_markers = (
        "march 31",
        "31 march",
        "31st march",
        "april 1",
        "1 april",
        "companies act, 2013",
        "reserve bank of india",
        "sebi",
        "crore",
        "lakh",
        "₹",
    )
    if any(marker in lowered for marker in indian_markers):
        return INDIA
    if "september 30" in lowered and "fiscal year" in lowered:
        return US_FEDERAL
    return CALENDAR
