"""Reading order and layout reconstruction.

PyMuPDF returns text in the order the PDF's content stream happens to store it. For a
single-column report that is fine. For a two-column central bank review it interleaves
columns mid-sentence, and for a chart slide it emits a pile of loose numbers with their
axis labels somewhere else entirely.

Page 9 of the Delhivery earnings deck extracts as:

    59% 63% 62% 24% 16% 19% ... 7,054 7,224 8,142 FY22 FY23 FY24 Express Parcel PTL ...

Every number in that page is real and every one of them is unattributable as written. This
module rebuilds the spatial relationships that the text layer threw away, so a model is
asked to read a page rather than to guess at a bag of tokens.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Any

from app.pipeline.parse import ParsedPage, Table, Word


@dataclass
class Line:
    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)

    def segmented(self, gap: float, separator: str = "   ·   ") -> str:
        """Line text with a separator wherever a wide horizontal gap appears.

        On a slide, three unrelated chart titles can share a y position. Joining them with
        a single space reads as one sentence; marking the gaps keeps them distinguishable.
        """
        if len(self.words) < 2:
            return self.text
        parts = [self.words[0].text]
        for previous, word in zip(self.words, self.words[1:], strict=False):
            parts.append(separator if word.x0 - previous.x1 > gap else " ")
            parts.append(word.text)
        return "".join(parts)

    @property
    def word_count(self) -> int:
        return len(self.words)

    @property
    def x0(self) -> float:
        return min(word.x0 for word in self.words)

    @property
    def x1(self) -> float:
        return max(word.x1 for word in self.words)

    @property
    def y0(self) -> float:
        return min(word.y0 for word in self.words)

    @property
    def y1(self) -> float:
        return max(word.y1 for word in self.words)


@dataclass
class Column:
    x0: float
    x1: float
    lines: list[Line] = field(default_factory=list)


@dataclass
class LayoutResult:
    rendition: str
    column_count: int
    line_count: int
    cluster_count: int
    details: dict[str, Any]


def _median_height(words: list[Word]) -> float:
    heights = [word.height for word in words if word.height > 0]
    if not heights:
        return 10.0
    return max(4.0, statistics.median(heights))


def group_lines(words: list[Word], tolerance: float | None = None) -> list[Line]:
    """Cluster words into text lines by vertical position."""
    if not words:
        return []
    threshold = tolerance if tolerance is not None else _median_height(words) * 0.6

    ordered = sorted(words, key=lambda word: (word.center_y, word.x0))
    lines: list[list[Word]] = [[ordered[0]]]
    for word in ordered[1:]:
        current = lines[-1]
        reference = statistics.median([item.center_y for item in current])
        if abs(word.center_y - reference) <= threshold:
            current.append(word)
        else:
            lines.append([word])

    return [Line(words=sorted(group, key=lambda word: word.x0)) for group in lines]


def vertical_corridors(
    page: ParsedPage,
    words: list[Word],
    *,
    max_gaps: int,
    min_width_ratio: float,
    coverage_floor_ratio: float,
    band_top: float = 0.0,
    band_bottom: float = 1.0,
) -> list[tuple[float, float]]:
    """Split the page on vertical whitespace corridors.

    Works on an occupancy histogram over x. A corridor counts only if it is wide enough to
    be a real gutter and carries almost no text down the page, which keeps ordinary word
    spacing from being mistaken for a column break.

    Prose and slides want different settings, so the thresholds are arguments rather than
    constants: a journal's two columns are separated by a tall thin gutter, while a row of
    charts is separated by several wide ones.
    """
    if len(words) < 12:
        return [(0.0, page.width)]

    # Running headers, slide titles, legends and footnotes span the full width by design.
    # Left in, a single footnote line welds every panel on the page into one, so corridors
    # are measured on the body band only. Words outside it are still assigned to a panel.
    body = [
        word
        for word in words
        if page.height * band_top <= word.center_y <= page.height * band_bottom
    ]
    if len(body) < 12:
        body = words

    bucket_width = max(2.0, page.width / 240)
    bucket_count = int(page.width / bucket_width) + 1
    occupancy = [0.0] * bucket_count

    for word in body:
        start = max(0, int(word.x0 / bucket_width))
        end = min(bucket_count - 1, int(word.x1 / bucket_width))
        for index in range(start, end + 1):
            occupancy[index] += word.height

    content_left = min(word.x0 for word in body)
    content_right = max(word.x1 for word in body)
    minimum_gutter = max(page.width * 0.02, _median_height(body) * 1.2)

    # The floor is a fraction of how much text a typical column carries, not an absolute
    # number. A gutter is rarely empty — a centred box heading or a full-width footnote
    # crosses it — so what identifies it is being far emptier than the text either side.
    # Scaling to the page's own density also makes this independent of page size and font.
    occupied = [value for value in occupancy if value > 0]
    if not occupied:
        return [(0.0, page.width)]
    typical = statistics.median(occupied)
    coverage_floor = typical * coverage_floor_ratio

    gaps: list[tuple[float, float]] = []
    run_start: int | None = None
    for index, value in enumerate(occupancy):
        position = index * bucket_width
        inside = content_left < position < content_right
        if value <= coverage_floor and inside:
            run_start = index if run_start is None else run_start
            continue
        if run_start is not None:
            gap_start = run_start * bucket_width
            gap_end = index * bucket_width
            if gap_end - gap_start >= minimum_gutter:
                gaps.append((gap_start, gap_end))
            run_start = None
    if run_start is not None:
        gap_start = run_start * bucket_width
        if content_right - gap_start >= minimum_gutter:
            gaps.append((gap_start, content_right))

    # Many narrow corridors mean a table, not a multi-column layout. Splitting there would
    # scatter every row across several blocks.
    if not gaps or len(gaps) > max_gaps:
        return [(0.0, page.width)]

    boundaries = [content_left]
    for gap_start, gap_end in gaps:
        boundaries.append((gap_start + gap_end) / 2)
    boundaries.append(content_right + 1)

    columns: list[tuple[float, float]] = []
    for index in range(len(boundaries) - 1):
        left, right = boundaries[index], boundaries[index + 1]
        if right - left >= page.width * min_width_ratio:
            columns.append((left, right))
    return columns or [(0.0, page.width)]


def detect_columns(page: ParsedPage, words: list[Word]) -> list[tuple[float, float]]:
    """Text columns for prose pages."""
    if len(words) < 40:
        return [(0.0, page.width)]
    return vertical_corridors(
        page, words, max_gaps=3, min_width_ratio=0.12, coverage_floor_ratio=0.35
    )


def _assign_to_columns(words: list[Word], columns: list[tuple[float, float]]) -> list[list[Word]]:
    buckets: list[list[Word]] = [[] for _ in columns]
    for word in words:
        center = (word.x0 + word.x1) / 2
        target = 0
        for index, (left, right) in enumerate(columns):
            if left <= center < right:
                target = index
                break
        else:
            # A word wider than any single column is a full-width heading or rule; it
            # belongs at the top of the first column rather than dropped.
            target = 0
        buckets[target].append(word)
    return buckets


# A cell that is a figure rather than a label. Deliberately permissive about what may
# surround the digits — currency marks, percent signs, accounting parentheses, footnote
# daggers — because the question here is only "is this column data or heading".
_NUMERIC_CELL = re.compile(r"^[^\w]*[-+(]?\s*[\d.,]*\d[\d.,]*\s*\)?\s*[^\w]*$")

# Header rows to consider before giving up. Financial tables stack two or three — a span of
# years over a row of "Actual / Est. / Proj." — and beyond that the run of label-only rows
# is far more likely to be a stub column than a header.
_MAX_HEADER_ROWS = 3

# Below this many data columns a model reliably tracks which figure sits under which
# heading from the grid alone, and spelling every cell out is a waste of context.
_WIDE_TABLE_COLUMNS = 3

# Ceiling on spelled-out cells for one table, so a long statement of accounts cannot
# crowd out the rest of the page.
_MAX_ADDRESSED_CELLS = 200


def _render_tables(tables: list[Table]) -> list[str]:
    """Serialise detected tables as pipe-delimited rows, then address their cells.

    Markdown alignment rows are omitted deliberately: they cost tokens on wide financial
    tables and add nothing a model needs to read the grid.

    The grid alone is not enough on a wide table. A figure in the fourth of six columns is
    only meaningful once it is joined to a heading that may be three rows above it, and
    that join is exactly what goes wrong — a value read off the "2024/25 Est." column and
    filed under "2023/24" is a well-formed fact that happens to be false, which is the
    worst kind for a system like this to produce. So each numeric cell of a wide table is
    also written out beside its own row label and column heading, where the pairing cannot
    come apart. The grid stays too; nothing is taken away, and a model that prefers to read
    the layout still can.
    """
    rendered: list[str] = []
    for index, table in enumerate(tables, start=1):
        lines = [f"[table {index}]"]
        for row in table.rows:
            lines.append(" | ".join(cell if cell else "-" for cell in row))

        addressed = _address_cells(table)
        if addressed:
            lines.append(f"[table {index} cells]")
            lines.extend(addressed)
        rendered.append("\n".join(lines))
    return rendered


def _address_cells(table: Table) -> list[str]:
    """Every numeric cell of a wide table written as `row label | column heading = value`."""
    header_depth = _header_depth(table.rows)
    if not header_depth:
        return []

    headers = _column_headers(table.rows[:header_depth], table.column_count)
    # Column zero carries the row labels, so it is not one of the data columns being
    # counted, and a table narrow enough to read straight off the grid is left alone.
    if sum(1 for header in headers[1:] if header) < _WIDE_TABLE_COLUMNS:
        return []

    addressed: list[str] = []
    for row in table.rows[header_depth:]:
        label = (row[0] if row else "").strip()
        # A row whose stub cell is blank is a spacer, a wrapped label or a continuation.
        # Inheriting the previous row's label would attach figures to the wrong line item,
        # so these keep their grid row and nothing more.
        if not label or _is_numeric_cell(label):
            continue
        for column, cell in enumerate(row[1:], start=1):
            value = (cell or "").strip()
            if not value or not _is_numeric_cell(value):
                continue
            header = headers[column] if column < len(headers) else ""
            addressed.append(f"{label} | {header} = {value}" if header else f"{label} = {value}")
            if len(addressed) >= _MAX_ADDRESSED_CELLS:
                return addressed
    return addressed


def _header_depth(rows: list[list[str]]) -> int:
    """How many leading rows are heading rather than data."""
    depth = 0
    for row in rows[:_MAX_HEADER_ROWS]:
        filled = [cell.strip() for cell in row if cell and cell.strip()]
        if len(filled) < 2 or any(_is_numeric_cell(cell) for cell in filled):
            break
        depth += 1
    # A table that is entirely headings was misdetected; treating all of it as a header
    # would address no cells anyway, but returning 0 says so plainly.
    return depth if depth < len(rows) else 0


def _column_headers(header_rows: list[list[str]], width: int) -> list[str]:
    """Collapse stacked heading rows into one heading per column.

    A year on one row above a basis on the next is a single heading split across two lines,
    and it is the whole heading that changes what the figures below it mean.
    """
    headers: list[str] = []
    for column in range(width):
        parts: list[str] = []
        for row in header_rows:
            cell = (row[column] if column < len(row) else "") or ""
            cell = cell.strip()
            if cell and cell not in parts:
                parts.append(cell)
        headers.append(" ".join(parts))
    return headers


def _is_numeric_cell(cell: str) -> bool:
    text = (cell or "").strip()
    return (
        bool(text)
        and bool(_NUMERIC_CELL.match(text))
        and any(character.isdigit() for character in text)
    )


def _spans_columns(line: Line, columns: list[tuple[float, float]], gutter: float) -> bool:
    """Whether a line is genuinely one run of text across a column boundary.

    Running heads, box titles and footnote rules are laid out across the full measure. Cut
    at the boundary they become two fragments — "ANNUAL" and "REPORT 2024-25" — which is
    worse than useless, because a fragment still looks like text and gets read as such.

    Extent alone cannot decide this. Grouping words into lines across the whole page also
    merges the left and right column's *body* lines whenever they sit at the same height,
    and those must stay separate. The distinguishing feature is continuity: a real
    full-width line has no gutter-sized gap in it, while two column lines sharing a y do.
    """
    if len(columns) < 2 or len(line.words) < 2:
        return False

    for _, boundary in columns[:-1]:
        if not (line.x0 < boundary < line.x1):
            continue
        crosses_with_gap = any(
            previous.x1 <= boundary <= word.x0 and word.x0 - previous.x1 >= gutter
            for previous, word in zip(line.words, line.words[1:], strict=False)
        )
        if not crosses_with_gap:
            return True
    return False


def reconstruct_flow(page: ParsedPage) -> LayoutResult:
    """Reading order for prose and table pages."""
    words = page.words
    if not words:
        return LayoutResult(rendition="", column_count=0, line_count=0, cluster_count=0, details={})

    columns = detect_columns(page, words)
    all_lines = group_lines(words)

    if len(columns) < 2:
        body = "\n".join(line.text for line in sorted(all_lines, key=lambda line: line.y0))
        return _finish_flow(page, body, columns, len(all_lines), 0)

    gutter = max(_median_height(words) * 1.2, page.width * 0.015)
    spanning = [line for line in all_lines if _spans_columns(line, columns, gutter)]
    spanning_words = {id(word) for line in spanning for word in line.words}
    column_words = [word for word in words if id(word) not in spanning_words]
    buckets = _assign_to_columns(column_words, columns)

    parts: list[str] = []
    if spanning:
        body = "\n".join(line.text for line in sorted(spanning, key=lambda line: line.y0))
        parts.append(f"[spanning the full page width]\n{body}")

    total_lines = len(spanning)
    for index, bucket in enumerate(buckets):
        if not bucket:
            continue
        lines = group_lines(bucket)
        total_lines += len(lines)
        body = "\n".join(line.text for line in lines)
        parts.append(f"[column {index + 1} of {len(columns)}]\n{body}")

    return _finish_flow(page, "\n\n".join(parts), columns, total_lines, len(spanning))


def _finish_flow(
    page: ParsedPage,
    rendition: str,
    columns: list[tuple[float, float]],
    line_count: int,
    spanning_count: int,
) -> LayoutResult:
    tables = _render_tables(page.tables)
    if tables:
        rendition = f"{rendition}\n\n" + "\n\n".join(tables)
    return LayoutResult(
        rendition=rendition.strip(),
        column_count=len(columns),
        line_count=line_count,
        cluster_count=0,
        details={
            "columns": [[round(left, 1), round(right, 1)] for left, right in columns],
            "spanning_lines": spanning_count,
            "tables": len(page.tables),
        },
    )


def _panel_of(center: float, panels: list[tuple[float, float]]) -> int:
    for index, (left, right) in enumerate(panels):
        if left <= center < right:
            return index
    return 0


def _aligned_columns(lines: list[Line], tolerance: float) -> list[list[Line]]:
    """Group lines that share a horizontal position, ordered top to bottom.

    This is the part that recovers a bar chart. A bar's value sits directly above its
    category label, so items sharing an x-centre are almost always the same data point read
    downwards. Emitting them together is what turns three loose numbers and three loose year
    labels into three attributable values.
    """
    if len(lines) < 2:
        return []

    ordered = sorted(lines, key=lambda line: (line.x0 + line.x1) / 2)
    groups: list[list[Line]] = []
    for line in ordered:
        center = (line.x0 + line.x1) / 2
        if groups:
            last = groups[-1]
            reference = sum((item.x0 + item.x1) / 2 for item in last) / len(last)
            if abs(center - reference) <= tolerance:
                last.append(line)
                continue
        groups.append([line])

    return [sorted(group, key=lambda line: line.y0) for group in groups if len(group) > 1]


def reconstruct_chart(page: ParsedPage) -> LayoutResult:
    """Reading order for chart slides.

    Proximity clustering does not work here. On a bar chart the value sits near the top of
    the plot area and its axis label sits at the bottom, often three hundred points away,
    so any threshold loose enough to join them also merges the chart with its neighbours.

    Two views are emitted instead, because neither is sufficient alone. Reading order keeps
    titles and footnotes intact but says nothing about which number belongs to which bar.
    Grouping by shared horizontal position recovers exactly that, at the cost of slicing a
    title in half when a chart is split per bar. Together they are unambiguous, and chart
    pages are short enough that carrying both costs almost nothing.
    """
    words = page.words
    if not words:
        return LayoutResult(rendition="", column_count=0, line_count=0, cluster_count=0, details={})

    height = _median_height(words)
    panels = vertical_corridors(
        page,
        words,
        max_gaps=5,
        min_width_ratio=0.08,
        coverage_floor_ratio=0.22,
        band_top=0.12,
        band_bottom=0.86,
    )

    lines = sorted(group_lines(words), key=lambda line: line.y0)
    gap = max(height * 2.2, page.width * 0.02)
    reading_order = "\n".join(f"  y={line.y0:>4.0f}  {line.segmented(gap)}" for line in lines)

    buckets: list[list[Word]] = [[] for _ in panels]
    for word in words:
        buckets[_panel_of((word.x0 + word.x1) / 2, panels)].append(word)

    tolerance = max(height * 1.4, page.width * 0.015)
    grouped: list[str] = []
    total_columns = 0
    for index, bucket in enumerate(buckets):
        if not bucket:
            continue
        # Running text is not chart furniture. Excluding it keeps the alignment groups to
        # the values and labels they are meant to pair.
        candidates = [line for line in group_lines(bucket) if line.word_count <= 5]
        for group in _aligned_columns(candidates, tolerance):
            total_columns += 1
            left, right = panels[index]
            items = " | ".join(f"{line.text} (y={line.y0:.0f})" for line in group)
            grouped.append(f"  [x {left:.0f}-{right:.0f}] {items}")

    sections = ["reading order (y is vertical position, top of page is 0):", reading_order]
    if grouped:
        sections.append(
            "vertically aligned groups (items sharing a horizontal position, read "
            "top to bottom; a bar value sits above its axis label):"
        )
        sections.append("\n".join(grouped))

    rendition = "\n".join(sections)
    tables = _render_tables(page.tables)
    if tables:
        rendition = f"{rendition}\n\n" + "\n\n".join(tables)

    return LayoutResult(
        rendition=rendition.strip(),
        column_count=len(panels),
        line_count=len(lines),
        cluster_count=total_columns,
        details={
            "panels": [[round(left, 1), round(right, 1)] for left, right in panels],
            "aligned_groups": total_columns,
            "tables": len(page.tables),
        },
    )


def reconstruct(page: ParsedPage, page_type: str) -> LayoutResult:
    from app.pipeline.classify import uses_chart_layout

    return reconstruct_chart(page) if uses_chart_layout(page_type) else reconstruct_flow(page)
