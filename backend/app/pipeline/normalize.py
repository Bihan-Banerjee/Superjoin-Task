"""Normalisation.

Converts a grounded candidate into the comparable form the linking stage works on:
a value in a class base unit, a period as a dated interval, a cleaned set of qualifiers,
and a basis. Everything the document actually wrote is preserved alongside, because the
surface form is what the evidence has to keep matching.

Two decisions worth naming.

Units are resolved with a precedence order — what is beside the number, then what the page
declares, then what the document declares — because that is the order of specificity a
reader applies. A figure printed as "8,142" on a page headed "(₹ in crore)" is in crore;
the same figure with "million" written next to it is not, regardless of the page heading.

Currencies are never converted. An exchange rate has its own date and source, and applying
one would put a number into the knowledge layer that appears in no document. Facts in
different currencies are reported as differing on the currency dimension instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core import periods as period_lib
from app.core.text import collapse_whitespace
from app.core.units import (
    COUNT_NOUNS,
    CURRENCY,
    Unit,
    is_bare_scale,
    normalize_currency,
    normalize_scale,
    parse_number,
    resolve_unit,
)


@dataclass
class DocumentContext:
    """Document-level defaults every fact in it inherits."""

    fiscal_convention: str = period_lib.CALENDAR
    default_currency: str | None = None
    default_scale: float | None = None
    reporting_basis: str | None = None
    as_of_date: str | None = None
    publisher: str | None = None
    title: str | None = None
    doc_type: str | None = None


@dataclass
class PageContext:
    unit_currency: str | None = None
    unit_scale: float | None = None


@dataclass
class NormalizedFact:
    statement: str
    kind: str
    subject: str
    predicate: str

    value_text: str | None
    value_number: float | None
    value_base: float | None

    unit_surface: str | None
    unit: Unit | None
    period: period_lib.Period
    period_label: str | None

    qualifiers: dict[str, str] = field(default_factory=dict)
    basis: str | None = None
    direction: str | None = None
    confidence: float = 0.6
    issues: list[str] = field(default_factory=list)

    @property
    def unit_class(self) -> str | None:
        return self.unit.unit_class if self.unit else None

    @property
    def currency(self) -> str | None:
        return self.unit.currency if self.unit else None

    def claim_key(self) -> str:
        """Text used for embedding and for coarse duplicate detection.

        Deliberately excludes the value: the point is to find facts that are *about* the
        same thing so their values can be compared. Including the value would make two
        conflicting statements look unrelated, which is exactly backwards.
        """
        qualifier_text = " ".join(
            f"{key}={value}" for key, value in sorted(self.qualifiers.items()) if value
        )
        parts = [
            self.subject,
            self.predicate,
            qualifier_text,
            self.period_label or "",
            self.basis or "",
        ]
        return " | ".join(part for part in parts if part).lower()


_DIRECTION_WORDS = {
    "increase": "increase",
    "increased": "increase",
    "rose": "increase",
    "grew": "increase",
    "growth": "increase",
    "up": "increase",
    "higher": "increase",
    "expanded": "increase",
    "decrease": "decrease",
    "decreased": "decrease",
    "fell": "decrease",
    "declined": "decrease",
    "decline": "decrease",
    "down": "decrease",
    "lower": "decrease",
    "contracted": "decrease",
    "narrowed": "decrease",
}

_BASIS_SYNONYMS = {
    "actual": "actual",
    "actuals": "actual",
    "reported": "actual",
    "audited": "actual",
    "estimate": "estimate",
    "estimated": "estimate",
    "est": "estimate",
    "advance estimate": "estimate",
    "projection": "projection",
    "projected": "projection",
    "proj": "projection",
    "forecast": "projection",
    "forecasted": "projection",
    "revised": "revised",
    "revised estimate": "revised",
    "re": "revised",
    "restated": "restated",
    "pro forma": "pro_forma",
    "proforma": "pro_forma",
    "pro_forma": "pro_forma",
    "budget": "budgeted",
    "budgeted": "budgeted",
    "budget estimate": "budgeted",
    "be": "budgeted",
    "target": "target",
    "provisional": "provisional",
    "preliminary": "provisional",
    "unspecified": None,
}

# Basis markers written inline rather than in a column header.
_BASIS_IN_TEXT = re.compile(
    r"\b(pro\s?forma|restated|revised estimate|revised|budget estimate|budgeted|"
    r"provisional|preliminary|projected|projection|forecast|estimated|estimate)\b",
    re.IGNORECASE,
)

_UNIT_NOISE = re.compile(r"^[\s(\[]+|[\s)\]]+$")


def normalize_candidate(
    candidate: dict[str, Any],
    quote: str,
    document: DocumentContext,
    page: PageContext,
) -> NormalizedFact:
    subject = collapse_whitespace(str(candidate.get("subject", "")))
    statement = collapse_whitespace(str(candidate.get("statement", "")))
    predicate, period_from_predicate = _split_period_from_predicate(
        collapse_whitespace(str(candidate.get("predicate", ""))).lower()
    )

    kind = str(candidate.get("kind") or "quantitative")
    value_text = collapse_whitespace(str(candidate.get("value_text", ""))) or None
    value_number = _read_number(candidate, value_text, kind)

    unit_surface = _clean_unit(candidate.get("unit"))
    unit = (
        _resolve(candidate, unit_surface, value_text, document, page)
        if value_number is not None
        else None
    )
    value_base = value_number * unit.factor if (value_number is not None and unit) else None

    period_label = (
        collapse_whitespace(str(candidate.get("period", ""))) or period_from_predicate or None
    )
    resolved_period = period_lib.parse_period(period_label, document.fiscal_convention)

    issues: list[str] = []
    if value_number is not None and unit is None:
        issues.append("unit_unresolved")
    if period_label and not resolved_period.resolved:
        issues.append("period_unresolved")

    return NormalizedFact(
        statement=statement,
        kind=str(candidate.get("kind") or "quantitative"),
        subject=subject,
        predicate=predicate,
        value_text=value_text,
        value_number=value_number,
        value_base=value_base,
        unit_surface=unit_surface,
        unit=unit,
        period=resolved_period,
        period_label=period_label,
        qualifiers=_clean_qualifiers(candidate.get("qualifiers")),
        basis=_resolve_basis(candidate, quote, document),
        direction=_detect_direction(statement, predicate),
        confidence=_confidence(candidate),
        issues=issues,
    )


def _read_number(candidate: dict[str, Any], value_text: str | None, kind: str) -> float | None:
    """The numeric value, if this fact has one.

    A categorical value is never scanned for digits. "Plot 5, Sector 44, Gurugram" contains
    numbers, and reading them turns an address into a quantity of 5 somethings — a fact that
    then gets compared arithmetically against other quantities. The extractor already says
    which kind of fact this is; trusting that is cheaper and more correct than guessing.
    """
    raw = candidate.get("value_number")
    if isinstance(raw, int | float) and not isinstance(raw, bool):
        return float(raw)
    if isinstance(raw, str) and raw.strip():
        parsed = parse_number(raw)
        if parsed is not None:
            return parsed
    if kind in _NON_NUMERIC_KINDS:
        return None
    return parse_number(value_text) if value_text else None


_NON_NUMERIC_KINDS = {"categorical", "relational", "definitional"}


def _clean_unit(raw: Any) -> str | None:
    if raw is None:
        return None
    text = _UNIT_NOISE.sub("", collapse_whitespace(str(raw)))
    return text or None


def _resolve(
    candidate: dict[str, Any],
    unit_surface: str | None,
    value_text: str | None,
    document: DocumentContext,
    page: PageContext,
) -> Unit | None:
    """Resolve a unit from most specific evidence to least.

    The declared scale is only inherited when the unit beside the number does not already
    carry one. Otherwise a page headed "(₹ in crore)" would multiply a figure explicitly
    labelled "million" by ten million, which is the exact error this stage exists to stop.
    """
    stated_currency = (
        normalize_currency(candidate.get("currency"))
        or _currency_from_text(unit_surface)
        or _currency_from_text(value_text)
    )

    # `resolve_unit` already absorbs a scale word written inside the unit label, so passing
    # one found there again would apply it twice and turn "8,142 Cr" into 8.142e17.
    scale_in_unit = _explicit_scale(unit_surface)
    inline_scale = scale_in_unit or _explicit_scale(value_text)
    stated_scale = None if scale_in_unit is not None else inline_scale

    unit = resolve_unit(unit_surface, scale=stated_scale, currency=stated_currency)
    if unit is None and value_text:
        unit = resolve_unit(
            _unit_from_value(value_text), scale=stated_scale, currency=stated_currency
        )

    # A declaration like "all amounts in Indian Rupees in million" is a statement about
    # *amounts*, so currency and scale are inherited under different conditions.
    #
    # The currency applies only where nothing else established one, so it is never pushed
    # onto a percentage or a shipment count.
    #
    # The scale applies to any monetary figure that did not carry its own — including one
    # whose currency the extractor did report, which is the common case for a figure printed
    # bare under a "(₹ in million)" heading. Gating scale on the currency being unknown too
    # leaves those figures a million times too small.
    # "Express Parcel shipments" given as "740 million" is 740 million shipments, not 740
    # million rupees. The measure name says what is being counted, so when it names a
    # countable thing and the figure carries only a scale, that noun becomes the unit and
    # the document's currency is not applied.
    unresolved = unit is None or is_bare_scale(unit)
    if unresolved:
        counted = _counted_noun(candidate)
        if counted:
            scale = unit.factor if unit else (inline_scale or 1.0)
            return resolve_unit(counted, scale=scale) or unit

    wants_currency = unresolved
    wants_scale = inline_scale is None and (unresolved or unit.unit_class == CURRENCY)
    if not wants_currency and not wants_scale:
        return unit

    inherited_currency = page.unit_currency or document.default_currency
    inherited_scale = page.unit_scale or document.default_scale

    currency = stated_currency or (inherited_currency if wants_currency else None)
    scale = inline_scale or (inherited_scale if wants_scale else None)
    if scale_in_unit is not None:
        scale = None  # already absorbed from the unit label
    if not currency and not scale:
        return unit

    return resolve_unit(unit_surface, scale=scale, currency=currency) or unit


# A period written into the front of a measure name, e.g. "FY24 EBITDA margin".
_PERIOD_PREFIX = re.compile(
    r"^\s*(?:"
    r"q[1-4]\s*(?:fy)?\s*\d{2,4}(?:\s*-\s*\d{2,4})?"
    r"|h[12]\s*(?:fy)?\s*\d{2,4}"
    r"|fy\s*\d{2,4}(?:\s*-\s*\d{2,4})?"
    r"|cy\s*\d{4}"
    r"|\d{4}\s*-\s*\d{2,4}"
    r")\s+",
    re.IGNORECASE,
)


def _split_period_from_predicate(predicate: str) -> tuple[str, str | None]:
    """Separate a period the extractor wrote into the measure name.

    "FY24 EBITDA margin" and "FY23 EBITDA margin" are one measure at two periods, and
    leaving the year in the name splits the registry so the two can never be compared. The
    instruction not to do this is in the prompt and is followed most of the time; this makes
    the outcome deterministic rather than dependent on that.

    The period is not discarded — it is returned so it can fill an empty period field.
    """
    match = _PERIOD_PREFIX.match(predicate)
    if not match:
        return predicate, None
    remainder = predicate[match.end() :].strip()
    if len(remainder) < 3:
        # The period was the whole name; keep it rather than reduce the measure to nothing.
        return predicate, None
    return remainder, match.group(0).strip()


def _counted_noun(candidate: dict[str, Any]) -> str | None:
    """The countable thing a measure names, if it names one.

    Read from the measure rather than the value, because that is where it is stated:
    "express parcel shipments" is a count of shipments however the figure beside it is
    written. Only the trailing words are considered — the head of the phrase is the noun.
    """
    predicate = collapse_whitespace(str(candidate.get("predicate", ""))).lower()
    if not predicate:
        return None
    words = re.findall(r"[a-z]+", predicate)
    for word in reversed(words[-2:]):
        if word in COUNT_NOUNS:
            return word
    return None


_SCALE_WORD = re.compile(
    r"\b(hundred|thousand|lakhs?|lacs?|million|millions|mn|crores?|cr|billion|billions|bn|"
    r"trillion|trillions|tn)\b|'000|’000",
    re.IGNORECASE,
)


def _explicit_scale(text: str | None) -> float | None:
    if not text:
        return None
    match = _SCALE_WORD.search(text)
    if not match:
        return None
    token = match.group(0).strip("'’")
    return normalize_scale("thousand" if token == "000" else token)


_CURRENCY_SYMBOL = re.compile(
    r"(₹|\$|€|£|¥)|\b(rs\.?|inr|usd|us\$|eur|gbp|jpy|sdr)\b", re.IGNORECASE
)


def _currency_from_text(text: str | None) -> str | None:
    if not text:
        return None
    match = _CURRENCY_SYMBOL.search(text)
    if not match:
        return None
    return normalize_currency(match.group(0))


_TRAILING_UNIT = re.compile(r"[\d,.\s]+([A-Za-z%°'’/]+.*)$")


def _unit_from_value(value_text: str) -> str | None:
    """Recover a unit the extractor left out but printed inside the value."""
    if value_text.strip().endswith("%"):
        return "%"
    match = _TRAILING_UNIT.search(value_text.strip())
    return match.group(1).strip() if match else None


def _clean_qualifiers(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, str] = {}
    for key, value in raw.items():
        name = collapse_whitespace(str(key)).lower().replace(" ", "_")
        text = collapse_whitespace(str(value))
        if not name or not text or text.lower() in {"none", "n/a", "null", "-", "unspecified"}:
            continue
        cleaned[name] = text
    return cleaned


def _resolve_basis(candidate: dict[str, Any], quote: str, document: DocumentContext) -> str | None:
    declared = str(candidate.get("basis", "")).strip().lower().replace("-", " ")
    if declared and declared in _BASIS_SYNONYMS:
        resolved = _BASIS_SYNONYMS[declared]
        if resolved:
            return resolved

    # The extractor often leaves basis unset while the evidence itself says "(Revised)" or
    # "pro forma". Reading it back off the quote recovers the distinction that separates a
    # genuine contradiction from a comparison of an estimate with an outcome.
    match = _BASIS_IN_TEXT.search(quote)
    if match:
        key = match.group(1).lower().replace("  ", " ")
        return _BASIS_SYNONYMS.get(key) or _BASIS_SYNONYMS.get(key.replace(" ", ""))

    if document.reporting_basis:
        return None
    return None


def _detect_direction(statement: str, predicate: str) -> str | None:
    haystack = f"{statement} {predicate}".lower()
    for word, direction in _DIRECTION_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", haystack):
            return direction
    return None


def _confidence(candidate: dict[str, Any]) -> float:
    try:
        value = float(candidate.get("confidence", 0.6))
    except (TypeError, ValueError):
        return 0.6
    return max(0.0, min(1.0, value))


def document_context_from_profile(profile: dict[str, Any]) -> DocumentContext:
    scale_text = str(profile.get("default_scale", "") or "").strip()
    convention = str(profile.get("fiscal_convention", "") or "").strip().lower()
    if convention not in period_lib.FISCAL_START_MONTH:
        convention = period_lib.CALENDAR
    return DocumentContext(
        fiscal_convention=convention,
        default_currency=normalize_currency(profile.get("default_currency")),
        default_scale=normalize_scale(scale_text) if scale_text else None,
        reporting_basis=str(profile.get("reporting_basis", "") or "").strip() or None,
        as_of_date=str(profile.get("as_of_date", "") or "").strip() or None,
        publisher=str(profile.get("publisher", "") or "").strip() or None,
        title=str(profile.get("title", "") or "").strip() or None,
        doc_type=str(profile.get("doc_type", "") or "").strip() or None,
    )
