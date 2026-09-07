"""Evidence grounding.

The assignment asks that every fact be linked to evidence in its source document. The
weak reading of that is to record which page a fact came from. The strong reading, taken
here, is that a fact is only admitted if the pipeline can independently find the exact
words that support it and point at them on the page.

So the model's quote is treated as a claim to be checked, not as a citation to be trusted:

1. The quote must be locatable in the page's raw text, allowing for the ligatures, soft
   hyphens and line-break hyphenation that PDF extraction introduces.
2. The value must actually appear inside that quote.
3. The quote must resolve to rectangles on the rendered page.

A candidate that fails any of these is rejected with a typed reason and stored. Those
rejections are not noise to be suppressed — they are the measurement of how much the
extractor got wrong, and they are where the required extraction-failure case comes from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.text import NO_MATCH, SpanMatch, locate, normalize
from app.core.units import parse_number

QUOTE_NOT_FOUND = "quote_not_found"
QUOTE_TOO_SHORT = "quote_too_short"
AMBIGUOUS_SHORT_QUOTE = "ambiguous_short_quote"
VALUE_ABSENT_FROM_QUOTE = "value_absent_from_quote"
MISSING_FIELDS = "missing_required_fields"
SUBJECT_UNRESOLVED = "subject_unresolved"
PREDICATE_UNRESOLVED = "predicate_unresolved"
LOW_CONFIDENCE = "low_confidence"
DUPLICATE = "duplicate_of_existing_fact"
PERIOD_UNRESOLVED = "period_unresolved"
UNIT_UNRESOLVED = "unit_unresolved"
UNATTRIBUTED_BY_MODEL = "unattributed_by_model"

# A quote has to pin the value to one place on the page. Length was the first proxy for
# that and it was the wrong one: on a metrics slide the evidence genuinely is a lone number
# in a table cell, because the label sits in a different column and no contiguous run of
# text contains both. What actually matters is whether the quote is *unambiguous*, so a
# short quote is accepted when it occurs exactly once on the page and rejected when it does
# not. See `_check_shape` and `AMBIGUOUS_SHORT_QUOTE`.
MINIMUM_QUOTE_CHARACTERS = 2
SHORT_QUOTE_TOKENS = 2
MINIMUM_CONFIDENCE = 0.25
MINIMUM_MATCH_SCORE = 82.0
# Characters either side of the matched span that still count as supporting the value.
VALUE_MATCH_MARGIN = 24


@dataclass
class GroundedFact:
    candidate: dict[str, Any]
    quote: str
    start: int
    end: int
    score: float
    exact: bool
    bboxes: list[list[float]] = field(default_factory=list)


@dataclass
class Rejection:
    reason: str
    detail: str
    candidate: dict[str, Any]


@dataclass
class GroundingOutcome:
    grounded: list[GroundedFact] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        total = len(self.grounded) + len(self.rejected)
        return len(self.grounded) / total if total else 0.0


_NUMBER_IN_TEXT = re.compile(r"[-+−]?\d[\d,.\s]*\d|\d")


def ground_candidates(
    candidates: list[dict[str, Any]],
    page_text: str,
    *,
    minimum_confidence: float = MINIMUM_CONFIDENCE,
) -> GroundingOutcome:
    outcome = GroundingOutcome()
    seen: set[str] = set()

    for candidate in candidates:
        rejection = _check_shape(candidate, minimum_confidence)
        if rejection is not None:
            outcome.rejected.append(rejection)
            continue

        quote = str(candidate.get("evidence_quote", "")).strip()

        # A short quote only grounds a fact if it points at one place. "18,540" beside a
        # "Pin-code reach" label in another column is perfectly good evidence when that
        # string appears once on the page, and no evidence at all when it appears five times.
        if len(quote.split()) < SHORT_QUOTE_TOKENS:
            occurrences = _count_occurrences(page_text, quote)
            if occurrences == 0:
                outcome.rejected.append(
                    Rejection(
                        reason=QUOTE_NOT_FOUND,
                        detail=f"the value {quote!r} does not appear on this page",
                        candidate=candidate,
                    )
                )
                continue
            if occurrences > 1:
                outcome.rejected.append(
                    Rejection(
                        reason=AMBIGUOUS_SHORT_QUOTE,
                        detail=(
                            f"the evidence is only {quote!r}, which appears {occurrences} "
                            "times on this page, so it does not identify which one the fact "
                            "refers to"
                        ),
                        candidate=candidate,
                    )
                )
                continue

        match = locate(page_text, quote, minimum_score=MINIMUM_MATCH_SCORE)
        if not match.found:
            match = _locate_fragment(page_text, quote, str(candidate.get("value_text", "")))
        if not match.found:
            outcome.rejected.append(
                Rejection(
                    reason=QUOTE_NOT_FOUND,
                    detail=(
                        "the quoted evidence does not appear on this page, so the fact "
                        "cannot be verified against the source"
                    ),
                    candidate=candidate,
                )
            )
            continue

        located = page_text[match.start : match.end]
        if not _value_supported(candidate, page_text, match.start, match.end):
            outcome.rejected.append(
                Rejection(
                    reason=VALUE_ABSENT_FROM_QUOTE,
                    detail=(
                        f"the value {candidate.get('value_text', '')!r} does not appear in "
                        f"the source text the evidence was matched to: {located.strip()!r}"
                    ),
                    candidate=candidate,
                )
            )
            continue

        fingerprint = _fingerprint(candidate, match.start)
        if fingerprint in seen:
            outcome.rejected.append(
                Rejection(
                    reason=DUPLICATE,
                    detail="an identical fact was already extracted from this page",
                    candidate=candidate,
                )
            )
            continue
        seen.add(fingerprint)

        outcome.grounded.append(
            GroundedFact(
                candidate=candidate,
                quote=located,
                start=match.start,
                end=match.end,
                score=match.score,
                exact=match.exact,
            )
        )

    return outcome


def _count_occurrences(page_text: str, quote: str) -> int:
    haystack = normalize(page_text).text
    needle = normalize(quote).text
    if not needle:
        return 0
    return haystack.count(needle)


# The layout renditions join spatially separate items with these markers so a model can see
# they are distinct. A model sometimes quotes across one, producing a "quote" that is real
# on screen and absent from the page.
_RENDITION_SEPARATORS = re.compile(r"\s*(?:\||·|/(?=\s)|\n)\s*")


def _locate_fragment(page_text: str, quote: str, value_text: str) -> SpanMatch:
    """Recover a quote the model assembled across the rendition's display separators.

    The page is shown to the model with spatially separate items marked off, and it will
    occasionally quote a whole row of them as evidence for one value. That string exists on
    the page in several places rather than one, so it cannot be located as written.

    Fragments that contain the value are tried first. Picking the longest instead would
    often land on a neighbouring cell, and the fact would then be rejected for a value that
    is genuinely on the page — the right fragment simply was not the one chosen.
    """
    fragments = [part.strip() for part in _RENDITION_SEPARATORS.split(quote) if part.strip()]
    if len(fragments) < 2:
        return NO_MATCH

    normalized_value = normalize(value_text).text if value_text else ""

    def carries_value(fragment: str) -> bool:
        return bool(normalized_value) and normalized_value in normalize(fragment).text

    ordered = sorted(fragments, key=lambda part: (not carries_value(part), -len(part)))
    for fragment in ordered:
        if len(fragment) < 2:
            continue
        match = locate(page_text, fragment, minimum_score=MINIMUM_MATCH_SCORE)
        if match.found:
            return match
    return NO_MATCH


def _check_shape(candidate: dict[str, Any], minimum_confidence: float) -> Rejection | None:
    required = ("statement", "subject", "predicate", "value_text", "evidence_quote")
    missing = [
        field_name for field_name in required if not str(candidate.get(field_name, "")).strip()
    ]
    if missing:
        return Rejection(
            reason=MISSING_FIELDS,
            detail=f"the extractor omitted required fields: {', '.join(missing)}",
            candidate=candidate,
        )

    quote = str(candidate["evidence_quote"]).strip()
    if len(quote) < MINIMUM_QUOTE_CHARACTERS:
        return Rejection(
            reason=QUOTE_TOO_SHORT,
            detail=f"the quoted evidence is {quote!r}, too little to locate anything",
            candidate=candidate,
        )

    subject = str(candidate["subject"]).strip()
    if len(subject) < 2 or subject.lower() in _VAGUE_SUBJECTS:
        return Rejection(
            reason=SUBJECT_UNRESOLVED,
            detail=f"the subject {subject!r} does not identify a specific entity",
            candidate=candidate,
        )

    predicate = str(candidate["predicate"]).strip()
    if len(predicate) < 3:
        return Rejection(
            reason=PREDICATE_UNRESOLVED,
            detail=f"the measure {predicate!r} is too vague to compare against anything",
            candidate=candidate,
        )

    confidence = _as_float(candidate.get("confidence"), default=0.6)
    if confidence < minimum_confidence:
        return Rejection(
            reason=LOW_CONFIDENCE,
            detail=f"the extractor reported confidence of {confidence:.2f}",
            candidate=candidate,
        )
    return None


_VAGUE_SUBJECTS = {
    "it",
    "the company",
    "the group",
    "company",
    "group",
    "they",
    "this",
    "the document",
    "n/a",
    "unknown",
    "-",
}


def _value_supported(
    candidate: dict[str, Any], page_text: str, start: int, end: int
) -> bool:
    """Check the value appears in the *source page*, inside the span the quote matched.

    Checked against the page and never against the model's own quote. A quote is a claim
    about the document, so validating a value against the text the model supplied would let
    it corroborate itself: write any number into the quote and the check passes. That is
    precisely the failure this guard exists to catch, because a plausible sentence carrying
    an invented figure looks completely correct in a table.

    Comparing on parsed numbers rather than strings means "8,142" still matches "8142" and
    "8 142", while a genuinely different figure is caught.
    """
    value_text = str(candidate.get("value_text", "")).strip()
    if not value_text:
        return False

    # A small margin either side: fuzzy matching can trim a boundary token, and the value
    # is sometimes the first or last thing in the quote.
    window = page_text[max(0, start - VALUE_MATCH_MARGIN) : end + VALUE_MATCH_MARGIN]

    if normalize(value_text).text in normalize(window).text:
        return True

    expected = candidate.get("value_number")
    expected_number = _as_float(expected, default=None) if expected is not None else None
    if expected_number is None:
        expected_number = parse_number(value_text)
    if expected_number is None:
        # Non-numeric values must appear literally; there is nothing else to compare.
        return False

    for raw in _NUMBER_IN_TEXT.findall(window):
        found = parse_number(raw)
        if found is None:
            continue
        if found == expected_number:
            return True
        scale = max(abs(found), abs(expected_number))
        if scale and abs(found - expected_number) / scale < 1e-6:
            return True
    return False


def _fingerprint(candidate: dict[str, Any], start: int) -> str:
    parts = (
        normalize(str(candidate.get("subject", ""))).text,
        normalize(str(candidate.get("predicate", ""))).text,
        normalize(str(candidate.get("value_text", ""))).text,
        normalize(str(candidate.get("period", ""))).text,
        str(sorted((candidate.get("qualifiers") or {}).items())),
        str(start),
    )
    return "|".join(parts)


def _as_float(value: Any, *, default: float | None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def unattributed_rejections(entries: list[dict[str, Any]]) -> list[Rejection]:
    """Turn the extractor's own "I could not attach this" reports into rejections.

    These are values the model saw and declined to guess about. Recording them makes the
    honest answer visible instead of leaving the page looking as though it held nothing.
    """
    rejections: list[Rejection] = []
    for entry in entries or []:
        value = str(entry.get("value_text", "")).strip()
        if not value:
            continue
        rejections.append(
            Rejection(
                reason=UNATTRIBUTED_BY_MODEL,
                detail=str(entry.get("reason", "")).strip()
                or "the extractor could not attach this value to a subject and measure",
                candidate={"value_text": value},
            )
        )
    return rejections
