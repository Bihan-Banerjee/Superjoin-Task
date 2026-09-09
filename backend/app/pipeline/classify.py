"""Page classification.

Two jobs. First, route each page to the right layout reconstruction: a two-column central
bank review and a chart slide need opposite treatment. Second, decide which pages are worth
spending a model call on at all.

The second job matters more than it sounds. A hundred-page filing carries a cover, a table
of contents, several pages of standard notice text and a signature block, none of which
contain a fact worth linking. Skipping them is the single cheapest performance win in the
pipeline and it costs nothing in recall, so the thresholds below are set to skip only when
the evidence is unambiguous.

All signals are structural: density, geometry, punctuation, digit share. Nothing keys off
a filename, a publisher or a phrase that only appears in this corpus.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.db.models import (
    PAGE_BOILERPLATE,
    PAGE_CHART,
    PAGE_EMPTY,
    PAGE_MIXED,
    PAGE_PROSE,
    PAGE_SCANNED,
    PAGE_TABLE,
    PAGE_TOC,
)
from app.pipeline.parse import ParsedPage

# A dot leader or a long run of spaces ending in a page number is what a contents entry
# looks like structurally, in any document that has one.
_TOC_ENTRY = re.compile(r"(?:\.{4,}|\s{6,}|…{2,})\s*\d{1,4}\s*$", re.MULTILINE)
_NUMERIC_TOKEN = re.compile(r"^[\(\)\-+₹$€£%]*[\d,.]+[%\)]?$")
_SENTENCE_END = re.compile(r"[.!?][\"')\]]?\s")


@dataclass
class PageSignals:
    page_type: str
    should_extract: bool
    reason: str
    metrics: dict[str, float]


def _numeric_share(page: ParsedPage) -> float:
    if not page.words:
        return 0.0
    numeric = sum(1 for word in page.words if _NUMERIC_TOKEN.match(word.text))
    return numeric / len(page.words)


def _table_coverage(page: ParsedPage) -> float:
    if not page.tables:
        return 0.0
    page_area = page.width * page.height
    if page_area <= 0:
        return 0.0
    covered = sum(
        max(0.0, table.bbox[2] - table.bbox[0]) * max(0.0, table.bbox[3] - table.bbox[1])
        for table in page.tables
    )
    return min(covered / page_area, 1.0)


def _sentence_density(page: ParsedPage) -> float:
    """Sentence terminators per hundred words. Prose runs high, slides run near zero."""
    if not page.words:
        return 0.0
    return len(_SENTENCE_END.findall(page.text)) / len(page.words) * 100


def _toc_score(page: ParsedPage) -> float:
    lines = [line for line in page.text.splitlines() if line.strip()]
    if len(lines) < 6:
        return 0.0
    return len(_TOC_ENTRY.findall(page.text)) / len(lines)


def _vector_density(page: ParsedPage) -> float:
    """Vector drawings per word. Charts are drawn, prose is not."""
    if not page.words:
        return float(page.vector_drawing_count)
    return page.vector_drawing_count / len(page.words)


def classify_page(page: ParsedPage) -> PageSignals:
    words = page.word_count
    metrics = {
        "words": float(words),
        "chars": float(page.char_count),
        "density": round(page.text_density, 3),
        "numeric_share": round(_numeric_share(page), 3),
        "table_coverage": round(_table_coverage(page), 3),
        "sentence_density": round(_sentence_density(page), 3),
        "toc_score": round(_toc_score(page), 3),
        "image_ratio": round(page.image_area_ratio, 3),
        "vector_density": round(_vector_density(page), 3),
        "ruling_lines": float(page.ruling_line_count),
        "tables": float(len(page.tables)),
    }

    if words < 6:
        # A blank page and a scanned one both yield nothing, and reporting them the same way
        # hides the only one of the two that is a limitation rather than an absence. A
        # document made entirely of scans would otherwise ingest "successfully" with no facts
        # and nothing to say about why.
        if page.looks_scanned:
            return PageSignals(
                PAGE_SCANNED,
                False,
                "an image with no text layer to read; OCR is off or did not recover text",
                metrics,
            )
        return PageSignals(PAGE_EMPTY, False, "no meaningful text on the page", metrics)

    if metrics["toc_score"] >= 0.3:
        return PageSignals(PAGE_TOC, False, "contents listing rather than content", metrics)

    # Section dividers and cover pages: a handful of large words and nothing else.
    if words < 25 and metrics["sentence_density"] < 1.0 and metrics["numeric_share"] < 0.25:
        return PageSignals(
            PAGE_BOILERPLATE,
            False,
            "title or divider page with too little text to carry a fact",
            metrics,
        )

    is_chart_like = (
        metrics["sentence_density"] < 1.2
        and metrics["numeric_share"] >= 0.18
        and words < 400
        and (metrics["image_ratio"] >= 0.10 or metrics["vector_density"] >= 0.35)
    )
    if is_chart_like:
        return PageSignals(PAGE_CHART, True, "sparse labelled figures over graphics", metrics)

    if (
        metrics["table_coverage"] >= 0.28
        or (metrics["tables"] >= 1 and metrics["numeric_share"] >= 0.30)
        or (metrics["ruling_lines"] >= 4 and metrics["numeric_share"] >= 0.25)
    ):
        page_type = PAGE_TABLE if metrics["sentence_density"] < 2.0 else PAGE_MIXED
        return PageSignals(page_type, True, "tabular figures dominate the page", metrics)

    if metrics["numeric_share"] >= 0.15 and metrics["sentence_density"] >= 1.2:
        return PageSignals(PAGE_MIXED, True, "prose carrying embedded figures", metrics)

    return PageSignals(PAGE_PROSE, True, "continuous prose", metrics)


def uses_chart_layout(page_type: str) -> bool:
    return page_type == PAGE_CHART


def is_vision_candidate(page_type: str, page: ParsedPage) -> bool:
    """Whether a rendered image would tell a model something the text layer cannot.

    Restricted to chart pages that actually carry graphics. Sending an image of a page whose
    text already reads cleanly doubles the cost of that page for no gain.
    """
    if page_type != PAGE_CHART:
        return False
    return page.image_area_ratio >= 0.08 or page.vector_drawing_count >= 40
