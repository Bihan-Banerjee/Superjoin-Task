"""Prompts.

Two rules shape all of them.

The first is that the model is a reader, not a source. It may report what a page says and
nothing else. No arithmetic, no filling in a unit from world knowledge, no completing a
figure it half-remembers. Everything it returns is checked against the page afterwards,
and the prompts say so, because a model told its output will be verified is measurably
more conservative about inventing.

The second is that nothing here names a company, a publisher, a measure or a document
type. The prompts describe *kinds* of things to look for, so the same instructions work on
a filing, a central bank review or a deck the system has never seen.
"""

from __future__ import annotations

from typing import Any

PROFILE_SYSTEM = """\
You read the opening pages of a document and report what kind of document it is and the \
conventions its figures follow.

You are establishing context that every later reading of this document depends on. The \
scale and currency defaults matter most: a report that says amounts are in millions and a \
deck that says the same amounts are in crore are not in disagreement, but only if both \
declarations are captured here.

Report only what the pages state or unambiguously show. Leave a field empty rather than \
guessing at it."""


def profile_prompt(filename_hint: str, metadata: dict[str, Any], excerpt: str) -> str:
    metadata_lines = "\n".join(f"  {key}: {value}" for key, value in sorted(metadata.items()))
    return f"""\
Embedded PDF metadata (may be absent, stale or wrong; treat it as a weak hint only):
{metadata_lines or "  none"}

Original filename: {filename_hint}

Opening pages:
---
{excerpt}
---

Report the document's identity and its reporting conventions.

For fiscal_convention, decide which fiscal year the document uses:
- "india" when the financial year runs April to March, so FY24, FY2023-24 and 2023-24 all \
end on 31 March 2024. Indian companies, Indian ministries and the Reserve Bank use this, \
and so do international publications when writing about India.
- "us_federal" when the year runs October to September.
- "calendar" when years are plain calendar years.

For default_scale and default_currency, look for a declaration such as "all amounts in \
Indian Rupees in million", "(₹ in crore)" or "US$ billion". These are usually in \
parentheses under a statement heading or in a note. Leave empty if no such declaration \
appears."""


EXTRACT_SYSTEM = """\
You extract facts from one page of a document.

A fact is something the page states that would still be meaningful and checkable a year \
from now, quoted on its own: a measured quantity, a date, a status, a relationship, a \
named attribute. Financial and statistical figures qualify. So do non-numeric statements \
such as who holds a role, where an office is registered, what a segment consists of, or \
when something took effect.

These are not facts: running heads, page numbers, table of contents entries, chart axis \
tick marks with no series attached, generic commentary with no measured content, and \
anything whose subject you cannot name.

Rules you must follow:

1. Report only what this page states. Never calculate, sum, convert, annualise or infer a \
value. If the page shows a number you cannot attach to a subject and a measure, put it in \
"unattributed" and explain why. That is a correct answer, not a failure.

2. evidence_quote must be copied character for character from the page content given to \
you. It must contain the value. Do not paraphrase, do not repair spelling, do not join \
text from separate parts of the page. Every quote is checked against the page afterwards \
and anything that does not match is discarded, so an invented quote loses the fact.

3. Record the value exactly as printed in value_text, and the same number without grouping \
separators in value_number.

3a. Name the measure at full length. A document that reports "revenue from services" and \
"revenue from traded goods" separately means different things by them, and shortening either \
to "revenue" makes two different quantities look like one. Keep every qualifying word the \
page uses.

4. Units and scale carry meaning. If the page or the document declares a default scale, \
apply it. State the unit you are using even when it comes from that declaration rather \
than from beside the number.

5. Periods carry meaning. Copy the period as the document writes it. A full year figure \
and a fourth quarter figure are different facts even when they sit side by side.

6. Qualifiers carry meaning. If a value applies to one segment, one geography, one basis \
of consolidation or one definition, record that. A number that is right for a segment and \
wrong for the whole is a contradiction waiting to happen.

7. basis distinguishes a reported outcome from an estimate, projection, revision or \
pro forma restatement. Columns headed "Est.", "Proj." or "RE" are not actuals. This is \
often the only thing separating a real disagreement from two publishers being consistent.

8. Set confidence honestly. Below 0.5 means you are unsure the attribution is right."""


def extract_prompt(
    *,
    document_context: str,
    page_number: int,
    printed_label: str | None,
    page_type: str,
    page_unit_note: str,
    rendition: str,
    has_image: bool,
) -> str:
    label = f" (printed page {printed_label})" if printed_label else ""
    guidance = _PAGE_GUIDANCE.get(page_type, "")
    if has_image:
        guidance = f"{guidance}{_VISION_GUIDANCE.get(page_type, '')}"
    image_note = (
        "\nA rendered image of this page is attached. Use it to resolve which value belongs "
        "to which series or label. The text below is still the authority for exact "
        "characters: evidence_quote must match the text, not what you read off the image.\n"
        if has_image
        else ""
    )
    unit_note = f"\n{page_unit_note}\n" if page_unit_note else ""

    return f"""\
Document context:
{document_context}

Page {page_number}{label}, classified as {page_type}.{unit_note}{image_note}
{guidance}
Page content:
---
{rendition}
---

Extract every fact this page states."""


_PAGE_GUIDANCE = {
    "chart_slide": """\
This page is a slide. Its text has been reassembled from position on the page, and the \
original reading order was lost, so read it carefully.

Two views of the same page follow. "reading order" lists lines top to bottom with their \
vertical position, and separates items that are far apart horizontally with a middle dot; \
that view keeps titles and footnotes intact. "vertically aligned groups" lists items that \
share a horizontal position, read downwards; in a bar chart that is a bar's value followed \
by whatever labels sit under it, so it is how you attach a number to its category.

Use the y positions to decide which title a value belongs to when one column of the page \
holds two charts stacked one above the other. A value at y=176 belongs to the chart titled \
at y=96, not the one titled at y=315.

Percentages stacked within one bar are segment shares of that bar. Attach them to a segment \
only when the mapping is unambiguous; when the legend order is the only clue, put them in \
"unattributed" instead of guessing.
""",
    "table": """\
This page is mostly tabular. Detected tables appear at the end as pipe-delimited rows.

Read the column headers before the numbers. A column headed with a year, a quarter, "Est." \
or "Proj." changes the period and the basis of every value beneath it. Row labels indented \
under a heading inherit that heading as part of their measure.

A wide table is followed by a "cells" list writing each figure out beside its own row label \
and column heading. That list is there to settle which heading a figure sits under, and \
nothing more. Take evidence_quote from the table rows or the page's prose, never from the \
cells list: those lines are assembled for you and do not appear on the page, so quoting one \
loses the fact.
""",
    "mixed": """\
This page mixes prose with figures. Figures embedded in a sentence usually take their \
period and scope from that sentence rather than from a heading.
""",
}


# Added only when an image of the page is attached. Kept separate from the page guidance
# because it licenses something the text-only instructions forbid: attaching a value to a
# series on evidence that is not in the text layer at all. That licence is worth granting
# for stacked charts, where the colour of a segment is the only thing that says which
# series it belongs to and a text-only reader has no way in. It is worth granting *only*
# with the image present, and only against a written account of what was matched, because
# the resulting claim is the one thing here no later check can verify against the page.
_VISION_GUIDANCE = {
    "chart_slide": """\
The image lets you settle attributions the text cannot. A stacked bar's segments carry no \
label in the text layer, and their order in the extracted text is the order they happened \
to be drawn, not the order of the legend.

Where a segment's colour matches a legend swatch, you may attach the value to that series \
and record the match in "attribution". Say what you matched, so a reader can check it \
against the image. Do the same for a value you placed by position — which chart on the \
page it sits in, which axis category it stands over.

This licence is narrow. Read the value itself from the text; the image is for deciding what \
the value belongs to. Where two segments are close in colour, where the legend has more \
entries than the bar has segments, or where the rendering is too small to be sure, the \
answer is still "unattributed" — a value filed under the wrong series is worse than one \
filed under none, because nothing downstream can tell that it is wrong.
"""
}


ADJUDICATE_SYSTEM = """\
You compare two facts that were extracted from documents and decide how they relate.

The mechanical checks have already run. Units have been converted to a common base, \
periods resolved to date ranges, and the values compared. This pair reached you because \
those checks were not conclusive, so the answer depends on reading the evidence.

Choose one verdict:

- corroborates: the same claim about the same thing over the same period, agreeing within \
the precision each is stated at. Different wording is fine. Different units are fine when \
they convert to the same amount.
- contradicts: the same claim about the same thing over the same period, and the values \
genuinely disagree. Only use this when you can rule out an explanation.
- reconciled_by_context: both are correct, and something in the context explains the \
difference. Name that something in dimension.
- refines: one is a more specific case of the other, such as a segment within a total or a \
quarter within a year.
- unrelated: they are not about the same thing and should not have been compared.

Choosing between contradicts and reconciled_by_context is the point of this task. Before \
you call something a contradiction, check each of these against the evidence:

- period: different years, quarters, or as-at dates; one cumulative and one for the period.
- unit_scale and currency: crore against million, percent against percentage points.
- scope and segment: consolidated against standalone, group against parent, one segment \
against the total, one geography against all.
- basis: an estimate, projection or budget compared with an outcome.
- vintage: two publishers reporting the same period at different times, where one has data \
the other did not have yet, or a figure has since been revised.
- definition: the same name for two different measurements, such as revenue including or \
excluding traded goods, or inflation on different indices.

If one of these explains the gap, the verdict is reconciled_by_context and dimension names \
it. If none does and the values disagree, it is a contradiction and you should say so \
plainly.

Ground the explanation in these two pieces of evidence. Do not use knowledge about these \
organisations from outside what you are shown."""


def adjudicate_prompt(left: dict[str, Any], right: dict[str, Any], rule_note: str) -> str:
    return f"""\
Fact A
{_render_fact(left)}

Fact B
{_render_fact(right)}

What the mechanical comparison found:
{rule_note}

Decide how these two facts relate."""


def _render_fact(fact: dict[str, Any]) -> str:
    lines = [
        f"  statement:  {fact.get('statement', '')}",
        f"  subject:    {fact.get('subject', '')}",
        f"  measure:    {fact.get('predicate', '')}",
        f"  value:      {fact.get('value_text', '')}",
    ]
    if fact.get("normalised"):
        lines.append(f"  normalised: {fact['normalised']}")
    if fact.get("period"):
        lines.append(f"  period:     {fact['period']}")
    if fact.get("qualifiers"):
        lines.append(f"  qualifiers: {fact['qualifiers']}")
    if fact.get("basis"):
        lines.append(f"  basis:      {fact['basis']}")
    lines.append(f"  source:     {fact.get('source', '')}")
    lines.append(f'  evidence:   "{fact.get("evidence_quote", "")}"')
    if fact.get("context"):
        lines.append(f"  surrounding text: ...{fact['context']}...")
    return "\n".join(lines)


MEASURE_LINKING_SYSTEM = """\
You maintain a registry of canonical measures for a knowledge layer.

Documents name the same measurement differently. "Revenue from services", "service \
revenue" and "revenue from operations (services)" are usually one measure. "Revenue" and \
"revenue growth" are not: one is an amount and the other is a rate of change. Neither are \
"revenue" and "revenue from services" when a document reports both, because one excludes \
something the other includes.

For each candidate phrase, either link it to an existing canonical measure or create a new \
one. Link when they measure the same quantity of the same kind, so that two values could \
be meaningfully compared. Create when they do not.

Merging two different measures is worse than keeping them apart: it makes the system report \
contradictions between numbers that were never the same number. When genuinely uncertain, \
create."""


def measure_linking_prompt(candidates: list[dict[str, Any]], existing: list[dict[str, Any]]) -> str:
    existing_block = (
        "\n".join(
            f"  - {item['name']} [{item.get('unit_class') or 'unclassified'}]"
            + (f" (also seen as: {', '.join(item['aliases'][:6])})" if item.get("aliases") else "")
            for item in existing
        )
        or "  none yet"
    )
    candidate_block = "\n".join(
        f'  - "{item["surface"]}" [{item.get("unit_class") or "unclassified"}]'
        f" e.g. {item.get('example', '')}"
        for item in candidates
    )
    return f"""\
Existing canonical measures:
{existing_block}

Candidate phrases to resolve:
{candidate_block}

Return one decision per candidate, copying the surface back exactly."""
