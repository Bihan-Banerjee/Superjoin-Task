"""PDF parsing.

Produces, for each page: the raw text exactly as the extractor sees it, word boxes for
evidence highlighting, detected tables, and enough geometry for the layout stage to
reconstruct a sane reading order.

The raw text is stored verbatim and never cleaned. Grounding depends on being able to
locate a quote in *this* string, so any tidying done here would have to be undone later.
"""

from __future__ import annotations

import math
import re
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf

from app.core.units import detect_unit_declaration


@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    block: int
    line: int
    index: int

    @property
    def center_y(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass
class Table:
    rows: list[list[str]]
    bbox: tuple[float, float, float, float]
    row_count: int
    column_count: int


@dataclass
class ParsedPage:
    page_number: int
    text: str
    words: list[Word]
    tables: list[Table]
    width: float
    height: float
    image_area_ratio: float
    vector_drawing_count: int
    ruling_line_count: int
    printed_label: str | None
    unit_currency: str | None = None
    unit_scale: float | None = None

    @property
    def word_count(self) -> int:
        return len(self.words)

    @property
    def char_count(self) -> int:
        return len(self.text)

    @property
    def text_density(self) -> float:
        """Characters per thousand square points. Separates prose from slides."""
        area = self.width * self.height
        return (len(self.text) / area * 1000) if area else 0.0


@dataclass
class ParsedDocument:
    path: Path
    page_count: int
    metadata: dict[str, Any]
    pages: list[ParsedPage]
    is_encrypted: bool = False
    needs_ocr: bool = False


_PRINTED_LABEL = re.compile(r"^\s*(?:page\s+)?([ivxlcdm]+|\d{1,4})\s*$", re.IGNORECASE)


def parse_pdf(
    path: Path,
    *,
    page_numbers: list[int] | None = None,
    workers: int = 1,
) -> ParsedDocument:
    """Parse a PDF into per-page structures.

    `page_numbers` restricts work to a subset, which the incremental re-ingest path uses
    to avoid re-reading a hundred pages to refresh three.

    Parsing is CPU-bound inside MuPDF and pages are independent, so `workers` above one
    spreads them across processes. Threads would not help: the work is native and the cost
    of shipping results back is small next to what the parse itself costs.
    """
    document = pymupdf.open(path)
    try:
        if document.is_encrypted and not document.authenticate(""):
            raise ValueError("PDF is password protected")
        page_count = document.page_count
        metadata = _clean_metadata(document.metadata or {})
        encrypted = document.is_encrypted
        targets = list(page_numbers) if page_numbers is not None else list(range(page_count))
        if workers <= 1 or len(targets) < _PARALLEL_THRESHOLD:
            pages = [_parse_page(document[index], index + 1) for index in targets]
        else:
            pages = None
    finally:
        document.close()

    if pages is None:
        pages = _parse_parallel(path, targets, workers)

    text_bearing = sum(1 for page in pages if page.word_count >= 8)
    return ParsedDocument(
        path=path,
        page_count=page_count,
        metadata=metadata,
        pages=pages,
        is_encrypted=encrypted,
        needs_ocr=bool(pages) and text_bearing / len(pages) < 0.25,
    )


_PARALLEL_THRESHOLD = 12


def _parse_parallel(path: Path, targets: list[int], workers: int) -> list[ParsedPage]:
    chunk_size = max(4, math.ceil(len(targets) / (workers * 2)))
    chunks = [targets[start : start + chunk_size] for start in range(0, len(targets), chunk_size)]

    results: dict[int, ParsedPage] = {}
    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_parse_chunk, str(path), chunk) for chunk in chunks]
            for future in futures:
                for page in future.result():
                    results[page.page_number] = page
    except Exception:
        # A pool can fail to start under a restricted environment or a frozen build. Falling
        # back keeps ingest working rather than failing over a performance optimisation.
        document = pymupdf.open(path)
        try:
            return [_parse_page(document[index], index + 1) for index in targets]
        finally:
            document.close()

    return [results[index + 1] for index in targets if index + 1 in results]


def _parse_chunk(path: str, indexes: list[int]) -> list[ParsedPage]:
    document = pymupdf.open(path)
    try:
        return [_parse_page(document[index], index + 1) for index in indexes]
    finally:
        document.close()


def _clean_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.strip() for key, value in raw.items() if isinstance(value, str) and value.strip()
    }


def _parse_page(page: pymupdf.Page, page_number: int) -> ParsedPage:
    rect = page.rect
    text = page.get_text("text")
    words = [
        Word(
            text=item[4],
            x0=float(item[0]),
            y0=float(item[1]),
            x1=float(item[2]),
            y1=float(item[3]),
            block=int(item[5]),
            line=int(item[6]),
            index=int(item[7]),
        )
        for item in page.get_text("words")
        if item[4].strip()
    ]

    image_area = _image_area(page, rect)
    drawings = _get_drawings(page)
    ruling_lines = _count_ruling_lines(drawings, float(rect.width))

    # Table detection costs around 450ms per page, roughly fifty times everything else on
    # this page put together. Run unconditionally it dominates ingest of a hundred-page
    # filing for no benefit, because most pages are prose. The gate below uses signals that
    # are already paid for — ruled lines and the share of numeric tokens — and only pays for
    # detection on pages that look like they hold a grid.
    tables = _extract_tables(page) if _likely_tabular(words, ruling_lines) else []

    currency, scale = detect_unit_declaration(text)

    return ParsedPage(
        page_number=page_number,
        text=text,
        words=words,
        tables=tables,
        width=float(rect.width),
        height=float(rect.height),
        image_area_ratio=image_area,
        vector_drawing_count=len(drawings),
        ruling_line_count=ruling_lines,
        printed_label=_printed_label(words, rect),
        unit_currency=currency,
        unit_scale=scale,
    )


def _get_drawings(page: pymupdf.Page) -> list[dict[str, Any]]:
    """Vector drawings. Native charts are vectors, so this is how a chart page announces
    itself when its bars never appear as raster images."""
    try:
        return page.get_drawings()
    except Exception:
        return []


def _count_ruling_lines(drawings: list[dict[str, Any]], page_width: float) -> int:
    """Count long horizontal rules. Tables are ruled; paragraphs are not."""
    if not drawings or page_width <= 0:
        return 0
    minimum_length = page_width * 0.25
    count = 0
    for item in drawings:
        rect = item.get("rect")
        if rect is None:
            continue
        try:
            width = float(rect.x1) - float(rect.x0)
            height = float(rect.y1) - float(rect.y0)
        except (AttributeError, TypeError, ValueError):
            continue
        if width >= minimum_length and height <= 2.5:
            count += 1
    return count


_TABLE_NUMERIC = re.compile(r"^[\(\)\-+₹$€£%]*[\d,.]+[%\)]?$")


def _likely_tabular(words: list[Word], ruling_lines: int) -> bool:
    if len(words) < 20:
        return False
    if ruling_lines >= 3:
        return True
    numeric = sum(1 for word in words if _TABLE_NUMERIC.match(word.text))
    return numeric / len(words) >= 0.18


def _extract_tables(page: pymupdf.Page) -> list[Table]:
    """Detect tables and strip the empty scaffolding the detector leaves behind.

    PyMuPDF's finder returns a dense grid with a cell for every ruling line it saw, so a
    six-column financial table often arrives as twenty columns of mostly `None`. Feeding
    that to a model wastes context and invites misalignment, so blank rows and columns are
    dropped before the table is serialised.
    """
    tables: list[Table] = []
    try:
        finder = page.find_tables()
    except Exception:
        return tables

    for table in getattr(finder, "tables", []):
        try:
            grid = table.extract()
        except Exception:
            continue
        cleaned = _clean_grid(grid)
        if not cleaned or len(cleaned) < 2:
            continue
        bbox = tuple(round(float(value), 2) for value in table.bbox)
        tables.append(
            Table(
                rows=cleaned,
                bbox=bbox,  # type: ignore[arg-type]
                row_count=len(cleaned),
                column_count=max(len(row) for row in cleaned),
            )
        )
    return tables


def _clean_grid(grid: list[list[Any]]) -> list[list[str]]:
    if not grid:
        return []

    normalised = [
        [" ".join(str(cell).split()) if cell is not None else "" for cell in row] for row in grid
    ]
    width = max((len(row) for row in normalised), default=0)
    if width == 0:
        return []
    normalised = [row + [""] * (width - len(row)) for row in normalised]

    keep_columns = [index for index in range(width) if any(row[index] for row in normalised)]
    if not keep_columns:
        return []

    rows = [[row[index] for index in keep_columns] for row in normalised]
    return [row for row in rows if any(cell for cell in row)]


def _image_area(page: pymupdf.Page, rect: pymupdf.Rect) -> float:
    """Fraction of the page covered by raster images.

    Slide decks and chart pages score high here, which is how the classifier tells them
    apart from prose without reading a word.
    """
    page_area = rect.width * rect.height
    if page_area <= 0:
        return 0.0
    covered = 0.0
    try:
        for info in page.get_image_info():
            bbox = info.get("bbox")
            if not bbox:
                continue
            width = max(0.0, bbox[2] - bbox[0])
            height = max(0.0, bbox[3] - bbox[1])
            covered += width * height
    except Exception:
        return 0.0
    return min(covered / page_area, 1.0)


def _printed_label(words: list[Word], rect: pymupdf.Rect) -> str | None:
    """The page number printed on the page, which usually differs from the PDF index.

    Curated excerpts keep the original numbering, so a fact cited as "page 56" needs both:
    the printed label for the citation, the PDF index to actually render the page.
    """
    if not words:
        return None
    footer_top = rect.height * 0.90
    header_bottom = rect.height * 0.08
    candidates = [
        word
        for word in words
        if (word.y0 >= footer_top or word.y1 <= header_bottom) and len(word.text) <= 8
    ]
    for word in candidates:
        match = _PRINTED_LABEL.match(word.text)
        if match:
            return match.group(1)
    return None


def render_page_png(path: Path, page_number: int, dpi: int = 140) -> bytes:
    """Render one page to PNG, for the vision extraction path and the evidence viewer."""
    document = pymupdf.open(path)
    try:
        page = document[page_number - 1]
        pixmap = page.get_pixmap(dpi=dpi)
        return pixmap.tobytes("png")
    finally:
        document.close()


def find_quote_boxes(
    path: Path, page_number: int, quote: str, *, max_boxes: int = 24
) -> list[list[float]]:
    """Locate a quote on a page and return its bounding boxes.

    Searches for the whole quote first. Long quotes routinely span a column break or a
    hyphenated word, which defeats a single search, so the fallback walks progressively
    shorter fragments and unions whatever it finds.
    """
    if not quote.strip():
        return []

    document = pymupdf.open(path)
    try:
        page = document[page_number - 1]
        boxes = _search(page, quote, max_boxes)
        if boxes:
            return boxes
        for fragment in _fragments(quote):
            boxes.extend(_search(page, fragment, max_boxes - len(boxes)))
            if len(boxes) >= max_boxes:
                break
        return _merge_boxes(boxes)
    finally:
        document.close()


def _search(page: pymupdf.Page, needle: str, limit: int) -> list[list[float]]:
    if limit <= 0 or len(needle.strip()) < 4:
        return []
    try:
        rects = page.search_for(needle.strip(), quads=False)
    except Exception:
        return []
    return [[round(float(value), 2) for value in rect] for rect in rects[:limit]]


def _fragments(quote: str, minimum_words: int = 6) -> list[str]:
    words = quote.split()
    if len(words) <= minimum_words:
        return []
    chunk = max(minimum_words, len(words) // 4)
    return [" ".join(words[start : start + chunk]) for start in range(0, len(words), chunk)]


def _merge_boxes(boxes: list[list[float]]) -> list[list[float]]:
    """Union boxes that sit on the same text line, so a highlight is one bar per line."""
    if not boxes:
        return []
    ordered = sorted(boxes, key=lambda box: (round(box[1], 1), box[0]))
    merged: list[list[float]] = [ordered[0]]
    for box in ordered[1:]:
        last = merged[-1]
        same_line = abs(box[1] - last[1]) < 3 and abs(box[3] - last[3]) < 3
        adjacent = box[0] - last[2] < 12
        if same_line and adjacent:
            merged[-1] = [
                min(last[0], box[0]),
                min(last[1], box[1]),
                max(last[2], box[2]),
                max(last[3], box[3]),
            ]
        else:
            merged.append(box)
    return merged
