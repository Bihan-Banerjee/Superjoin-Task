"""Table serialisation.

The grid a detector returns and the grid a reader sees are not the same thing. These tests
cover the gap: which rows are heading, how stacked headings collapse, and when a figure is
worth writing out beside the heading it belongs to.
"""

from app.pipeline.layout import _render_tables
from app.pipeline.parse import Table


def table(rows: list[list[str]]) -> Table:
    return Table(
        rows=rows,
        bbox=(0.0, 0.0, 100.0, 100.0),
        row_count=len(rows),
        column_count=max(len(row) for row in rows),
    )


ECONOMIC_OUTLOOK = [
    ["", "2023/24", "2024/25", "2025/26"],
    ["", "Actual", "Est.", "Proj."],
    ["Real GDP growth", "8.2", "6.5", "6.5"],
    ["Headline inflation", "5.4", "4.9", "4.4"],
    ["Current account balance", "-0.7", "-1.0", "-1.2"],
]


def test_a_stacked_heading_becomes_one_heading_per_column():
    """The year and the basis under it are one heading split across two rows.

    Reading the 6.5 in the third column as 2024/25 rather than 2025/26 is the whole
    difference between a fact and a plausible falsehood.
    """
    rendered = _render_tables([table(ECONOMIC_OUTLOOK)])[0]
    assert "Real GDP growth | 2024/25 Est. = 6.5" in rendered
    assert "Real GDP growth | 2025/26 Proj. = 6.5" in rendered


def test_the_grid_survives_alongside_the_addressed_cells():
    """Nothing is taken away; a model that reads the layout still can."""
    rendered = _render_tables([table(ECONOMIC_OUTLOOK)])[0]
    assert "Real GDP growth | 8.2 | 6.5 | 6.5" in rendered


def test_a_narrow_table_is_not_spelled_out():
    """Two columns need no help, and spelling them out is spent context."""
    rendered = _render_tables([table([["Measure", "FY24"], ["Revenue", "8,142"]])])[0]
    assert "cells]" not in rendered


def test_a_row_with_no_label_is_left_in_the_grid_only():
    """Inheriting the row above would file figures under the wrong line item."""
    rows = [
        ["", "2023/24", "2024/25", "2025/26"],
        ["", "Actual", "Est.", "Proj."],
        ["Real GDP growth", "8.2", "6.5", "6.5"],
        ["", "1.1", "2.2", "3.3"],
    ]
    rendered = _render_tables([table(rows)])[0]
    assert "= 1.1" not in rendered
    assert "| 1.1 | 2.2 | 3.3" in rendered


def test_a_table_that_opens_with_figures_has_no_heading_to_address_by():
    rows = [
        ["Revenue", "8,142", "7,224", "7,054"],
        ["Expenses", "7,900", "7,100", "6,900"],
    ]
    rendered = _render_tables([table(rows)])[0]
    assert "cells]" not in rendered


def test_accounting_parentheses_and_currency_marks_count_as_figures():
    rows = [
        ["", "FY22", "FY23", "FY24"],
        ["Profit after tax", "(1,011)", "(1,008)", "₹249"],
    ]
    rendered = _render_tables([table(rows)])[0]
    assert "Profit after tax | FY22 = (1,011)" in rendered
    assert "Profit after tax | FY24 = ₹249" in rendered
