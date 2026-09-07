"""Text normalisation and span location.

Everything in the grounding stage depends on being able to find a model-supplied quote
inside a PDF page and report *where in the original text* it sits. PDF text carries
ligatures, soft hyphens, line-break hyphenation, non-breaking spaces and tab-indented
paragraph numbers, none of which survive a naive string comparison.

The approach is to normalise into a canonical form while carrying an index map back to
the original offsets, match in normalised space, then translate the result back. That
keeps matching tolerant without losing the ability to point at exact characters.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

_PUNCTUATION_FOLD = {
    "‘": "'",
    "’": "'",
    "‚": "'",
    "‛": "'",
    "“": '"',
    "”": '"',
    "„": '"',
    "‟": '"',
    "‐": "-",
    "‑": "-",
    "‒": "-",
    "–": "-",
    "—": "-",
    "―": "-",
    "−": "-",
    " ": " ",
    " ": " ",
    " ": " ",
    " ": " ",
    " ": " ",
    " ": " ",
    " ": " ",
    "﻿": "",
    "­": "",
    "​": "",
    "‌": "",
    "‍": "",
}

_WHITESPACE = frozenset(" \t\r\n\v\f")


@dataclass(frozen=True)
class NormalizedText:
    """Canonical text plus a per-character map back into the source string."""

    text: str
    index_map: tuple[int, ...]
    source_length: int

    def to_source_span(self, start: int, end: int) -> tuple[int, int]:
        if not self.index_map:
            return 0, 0
        start = max(0, min(start, len(self.text)))
        end = max(start, min(end, len(self.text)))
        if start >= len(self.index_map):
            return self.source_length, self.source_length
        source_start = self.index_map[start]
        if end <= start:
            return source_start, source_start
        if end - 1 < len(self.index_map):
            source_end = self.index_map[end - 1] + 1
        else:
            source_end = self.source_length
        return source_start, max(source_start, source_end)


def normalize(text: str) -> NormalizedText:
    """Lowercase, fold punctuation, collapse whitespace, repair line-break hyphenation."""
    out: list[str] = []
    index_map: list[int] = []
    pending_hyphen = False
    last_was_space = True

    for position, raw_char in enumerate(text):
        char = _PUNCTUATION_FOLD.get(raw_char, raw_char)
        if char == "":
            continue

        if char in _WHITESPACE:
            # A hyphen immediately before a line break is almost always word wrapping.
            if pending_hyphen and raw_char in "\r\n":
                out.pop()
                index_map.pop()
                pending_hyphen = False
                continue
            if not last_was_space:
                out.append(" ")
                index_map.append(position)
                last_was_space = True
            pending_hyphen = False
            continue

        pending_hyphen = char == "-"

        for expanded in unicodedata.normalize("NFKC", char):
            folded = _PUNCTUATION_FOLD.get(expanded, expanded)
            if not folded or folded in _WHITESPACE:
                continue
            out.append(folded.lower())
            index_map.append(position)
        last_was_space = False

    while out and out[-1] == " ":
        out.pop()
        index_map.pop()

    return NormalizedText(
        text="".join(out),
        index_map=tuple(index_map),
        source_length=len(text),
    )


@dataclass(frozen=True)
class SpanMatch:
    start: int
    end: int
    score: float
    exact: bool

    @property
    def found(self) -> bool:
        return self.end > self.start


NO_MATCH = SpanMatch(start=0, end=0, score=0.0, exact=False)


def locate(haystack: str, needle: str, minimum_score: float = 82.0) -> SpanMatch:
    """Find `needle` inside `haystack`, returning a span in *haystack* coordinates.

    Tries an exact normalised substring first because that is both the common case and
    the cheapest. Falls back to a windowed edit-distance search, which recovers quotes
    the model paraphrased slightly or truncated mid-token.
    """
    if not needle.strip() or not haystack.strip():
        return NO_MATCH

    normalized_haystack = normalize(haystack)
    normalized_needle = normalize(needle).text
    if not normalized_needle:
        return NO_MATCH

    offset = normalized_haystack.text.find(normalized_needle)
    if offset >= 0:
        start, end = normalized_haystack.to_source_span(offset, offset + len(normalized_needle))
        return SpanMatch(start=start, end=end, score=100.0, exact=True)

    window = _best_window(normalized_haystack.text, normalized_needle)
    if window is None:
        return NO_MATCH

    window_start, window_end, score = window
    if score < minimum_score:
        return NO_MATCH
    start, end = normalized_haystack.to_source_span(window_start, window_end)
    return SpanMatch(start=start, end=end, score=score, exact=False)


def _best_window(haystack: str, needle: str) -> tuple[int, int, float] | None:
    """Slide a needle-sized window across the haystack and keep the best edit-distance fit.

    Steps by a fraction of the needle length rather than one character; a full scan on a
    3,000-character page for every candidate quote is measurably slower and buys nothing,
    because the refinement pass below recovers the exact boundaries anyway.
    """
    needle_length = len(needle)
    if needle_length > len(haystack):
        score = float(fuzz.ratio(needle, haystack))
        return (0, len(haystack), score) if score > 0 else None

    coarse_step = max(1, needle_length // 4)
    best_start = 0
    best_score = -1.0
    for start in range(0, len(haystack) - needle_length + 1, coarse_step):
        window = haystack[start : start + needle_length]
        score = Levenshtein.normalized_similarity(needle, window) * 100
        if score > best_score:
            best_score = score
            best_start = start

    refine_from = max(0, best_start - coarse_step)
    refine_to = min(len(haystack) - needle_length, best_start + coarse_step)
    for start in range(refine_from, refine_to + 1):
        window = haystack[start : start + needle_length]
        score = Levenshtein.normalized_similarity(needle, window) * 100
        if score > best_score:
            best_score = score
            best_start = start

    # Let the window breathe: quotes often drift in length from the source because of
    # how whitespace and footnote markers were handled on the way through the model.
    best_end = best_start + needle_length
    for delta in (-12, -8, -4, 4, 8, 12):
        candidate_end = best_end + delta
        if not best_start < candidate_end <= len(haystack):
            continue
        score = Levenshtein.normalized_similarity(needle, haystack[best_start:candidate_end]) * 100
        if score > best_score:
            best_score = score
            best_end = candidate_end

    return best_start, best_end, best_score


def contains_normalized(haystack: str, needle: str) -> bool:
    if not needle:
        return False
    return normalize(needle).text in normalize(haystack).text


def similarity(left: str, right: str) -> float:
    return float(fuzz.token_set_ratio(normalize(left).text, normalize(right).text))


_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def expand_to_context(text: str, start: int, end: int, radius: int = 400) -> str:
    """Widen a span into a readable window for the adjudicator prompt."""
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    snippet = text[left:right]
    if left > 0:
        parts = _SENTENCE_BOUNDARY.split(snippet, maxsplit=1)
        if len(parts) == 2 and len(parts[1]) > (end - start):
            snippet = parts[1]
    return snippet.strip()


def collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def truncate(text: str, limit: int, suffix: str = "...") -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))].rstrip() + suffix
