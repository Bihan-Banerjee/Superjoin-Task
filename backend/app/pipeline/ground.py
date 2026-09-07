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

from app.core.text import locate, normalize
from app.core.units import parse_number

QUOTE_NOT_FOUND = "quote_not_found"
QUOTE_TOO_SHORT = "quote_too_short"
VALUE_ABSENT_FROM_QUOTE = "value_absent_from_quote"
MISSING_FIELDS = "missing_required_fields"
SUBJECT_UNRESOLVED = "subject_unresolved"
PREDICATE_UNRESOLVED = "predicate_unresolved"
LOW_CONFIDENCE = "low_confidence"
DUPLICATE = "duplicate_of_existing_fact"
PERIOD_UNRESOLVED = "period_unresolved"
UNIT_UNRESOLVED = "unit_unresolved"
UNATTRIBUTED_BY_MODEL = "unattributed_by_model"

MINIMUM_QUOTE_CHARACTERS = 12
MINIMUM_CONFIDENCE = 0.25
MINIMUM_MATCH_SCORE = 82.0


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
        match = locate(page_text, quote, minimum_score=MINIMUM_MATCH_SCORE)
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
        if not _value_supported(candidate, located, quote):
            outcome.rejected.append(
                Rejection(
                    reason=VALUE_ABSENT_FROM_QUOTE,
                    detail=(
                        f"the value {candidate.get('value_text', '')!r} is not present in "
                        "the evidence that was quoted for it"
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
            detail=(
                f"the quoted evidence is {len(quote)} characters, too little to identify "
                "what the value measures"
            ),
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


def _value_supported(candidate: dict[str, Any], located: str, original_quote: str) -> bool:
    """Check the value is inside the evidence, not merely adjacent to it.

    A model that returns a plausible sentence and an unrelated number is the failure mode
    that matters most, because the result looks correct in a table. Comparing on parsed
    numbers rather than on strings means "8,142" still matches "8142" and " 8 142 ", while
    a genuinely different figure is caught.
    """
    value_text = str(candidate.get("value_text", "")).strip()
    if not value_text:
        return False

    haystack = normalize(f"{located} {original_quote}").text
    if normalize(value_text).text in haystack:
        return True

    expected = candidate.get("value_number")
    expected_number = _as_float(expected, default=None) if expected is not None else None
    if expected_number is None:
        expected_number = parse_number(value_text)
    if expected_number is None:
        # Non-numeric values must appear literally; there is nothing else to compare.
        return False

    for raw in _NUMBER_IN_TEXT.findall(f"{located} {original_quote}"):
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
