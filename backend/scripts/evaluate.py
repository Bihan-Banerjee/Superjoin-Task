"""Report what the knowledge layer contains and how well it was built.

Three things, in increasing order of how much they are worth.

`metrics` prints what the pipeline recorded — grounding pass rate, normalisation coverage,
the relation mix, model cost. Cheap, and computed from the run rather than claimed.

`audit` independently re-verifies every stored fact against its source page. The pipeline
already checked this at ingest, so a failure here means something drifted afterwards: a
prompt or threshold changed, a page was re-parsed differently, or a bug was introduced. It
is the regression test for grounding.

`sample` writes a worksheet of randomly chosen facts for manual checking. Grounding pass
rate says the evidence is real; it says nothing about whether the fact was attached to the
right measure or period. Only a person reading the page can answer that, and this makes
doing so systematic rather than anecdotal.

    python scripts/evaluate.py metrics
    python scripts/evaluate.py audit
    python scripts/evaluate.py cases
    python scripts/evaluate.py sample --size 40 --out ../docs/precision-sample.md
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.core.text import locate  # noqa: E402
from app.db.engine import session_scope  # noqa: E402
from app.db.models import Document, Fact, Page  # noqa: E402
from app.main import configure_logging  # noqa: E402

logger = logging.getLogger("evaluate")


def _rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def _row(label: str, value: Any, note: str = "") -> None:
    print(f"  {label:<38} {value!s:>14}  {note}")


def metrics() -> int:
    from app.api.evaluation import evaluation as build

    with session_scope() as session:
        report = build(session)

    corpus = report["corpus"]
    grounding = report["grounding"]
    normalisation = report["normalisation"]
    relations = report["relations"]
    registry = report["registry"]
    usage = report["model_usage"]

    if not corpus["documents"]:
        print("Nothing has been ingested yet.")
        return 1

    _rule("Corpus")
    _row("Documents", corpus["documents"])
    _row("Pages", corpus["pages"])
    _row("Pages sent to a model", corpus["pages_extracted"], f"of {corpus['pages']}")
    for entry in corpus["page_types"]:
        _row(f"  {entry['page_type']}", entry["count"])

    _rule("Grounding")
    _row("Candidate facts proposed", grounding["candidates_proposed"])
    _row("Kept", grounding["facts_kept"])
    _row("Rejected", grounding["candidates_rejected"])
    _row("Pass rate", f"{grounding['pass_rate']:.1%}")
    _row("Quotes matched exactly", f"{grounding['exact_match_rate']:.1%}")
    _row("Located on the page", f"{grounding['page_location_rate']:.1%}", "highlightable")

    _rule("Why candidates were rejected")
    for entry in report["rejections_by_reason"]:
        _row(entry["reason"], entry["count"])

    _rule("Normalisation")
    _row("Numeric facts", normalisation["numeric"], f"of {normalisation['facts']}")
    _row("Unit resolved", f"{normalisation['unit_resolution_rate']:.1%}", "of numeric")
    _row("Period resolved", f"{normalisation['period_resolution_rate']:.1%}")
    _row("Measure assigned", f"{normalisation['measure_assignment_rate']:.1%}")

    _rule("Relations")
    _row("Total", relations["total"])
    _row("Across documents", relations["cross_document"])
    for entry in relations["by_type"]:
        _row(f"  {entry['type']}", entry["count"])
    _rule("Explaining dimension")
    for entry in relations["by_dimension"]:
        if entry["dimension"] != "none":
            _row(f"  {entry['dimension']}", entry["count"])
    _rule("How each verdict was reached")
    for entry in relations["by_decision"]:
        _row(f"  {entry['decided_by']}", entry["count"])
    for entry in relations["by_rule"]:
        _row(f"    rule {entry['rule_id']}", entry["count"])

    _rule("Registry")
    _row("Measures", registry["measures"])
    _row("In more than one document", registry["measures_in_multiple_documents"])
    _row("Entities", registry["entities"])
    _row("Qualifier dimensions", registry["qualifier_keys"])

    _rule("Model usage")
    _row("Calls", usage["calls"])
    _row("Billed", usage["billed_calls"], f"{usage['cache_hit_rate']:.0%} served from cache")
    _row("Failed", usage["failed_calls"])
    _row("Input tokens", f"{usage['input_tokens']:,}")
    _row("Output tokens", f"{usage['output_tokens']:,}")
    _row("Median latency", f"{usage['median_latency_ms']} ms")

    _rule("Coverage by document")
    for entry in report["coverage"]:
        kept = entry["facts"] + entry["rejections"]
        share = f"{entry['facts'] / kept:.0%} kept" if kept else "—"
        print(
            f"  {entry['title'][:46]:<46} {entry['facts']:>5} facts  "
            f"{entry['facts_per_extracted_page']:>5.2f}/page  {share}"
        )
    print()
    return 0


def audit() -> int:
    """Re-verify every fact against its page, independently of ingest."""
    failures: list[tuple[int, str, str]] = []
    checked = 0

    with session_scope() as session:
        pages = {page.id: page for page in session.scalars(select(Page))}
        titles = {
            document.id: document.title or document.filename
            for document in session.scalars(select(Document))
        }

        for fact in session.scalars(select(Fact)):
            checked += 1
            page = pages.get(fact.page_id)
            if page is None:
                failures.append((fact.id, "page missing", fact.statement))
                continue

            if fact.evidence_start is not None and fact.evidence_end is not None:
                stored = page.text[fact.evidence_start : fact.evidence_end]
                if stored != fact.evidence_quote:
                    failures.append(
                        (fact.id, "stored span no longer matches the quote", fact.statement)
                    )
                    continue

            if not locate(page.text, fact.evidence_quote).found:
                failures.append((fact.id, "quote no longer locatable", fact.statement))
                continue

            if fact.value_text and fact.value_number is not None:
                window = page.text[
                    max(0, (fact.evidence_start or 0) - 24) : (fact.evidence_end or 0) + 24
                ]
                if fact.value_text not in window and fact.value_text.replace(",", "") not in (
                    window.replace(",", "")
                ):
                    failures.append(
                        (fact.id, "value not present in the source span", fact.statement)
                    )

    if not checked:
        print("Nothing has been ingested yet.")
        return 1

    print(f"\nRe-verified {checked} facts against their source pages.")
    print(f"  passed: {checked - len(failures)}")
    print(f"  failed: {len(failures)}")

    for fact_id, reason, statement in failures[:25]:
        print(f"    fact {fact_id}: {reason} — {statement[:70]}")
    if len(failures) > 25:
        print(f"    ... and {len(failures) - 25} more")

    verdict = "PASS: grounding is intact" if not failures else "FAIL: grounding is broken"
    print(f"\n  {verdict}")
    print(f"  documents in the layer: {len(titles)}\n")
    return 1 if failures else 0


def cases(out: Path | None) -> int:
    """Print the four required cases as the API derives them."""
    from app.api.cases import get_cases

    with session_scope() as session:
        report = get_cases(session)

    lines: list[str] = []
    missing = 0

    for index, block in enumerate(report["cases"], start=1):
        lines += ["", f"{index}. {block['title']}", "=" * (len(block["title"]) + 3), ""]
        lines.append(block["description"])
        lines.append("")

        if not block["found"]:
            missing += 1
            lines.append("  NOT FOUND in the current knowledge layer.")
            continue

        if block["key"] == "failure":
            summary = block.get("summary") or {}
            total = summary.get("facts_kept", 0) + summary.get("candidates_rejected", 0)
            lines.append(
                f"  Of {total:,} candidate facts, {summary.get('facts_kept', 0):,} were kept "
                f"and {summary.get('candidates_rejected', 0):,} refused "
                f"({summary.get('grounding_pass_rate', 0):.1%} grounded)."
            )
            lines.append("")
            for row in block.get("by_reason", []):
                lines.append(f"    {row['count']:>4}  {row['reason']}")
                if row.get("explanation"):
                    lines.append(f"          {row['explanation']}")
            continue

        for example in block["examples"][:2]:
            left, right = example["left"], example["right"]
            lines += [
                f"  [{example['type']}]  dimension: {example['dimension']}  "
                f"decided by: {example['decided_by']}"
                + (f" ({example['rule_id']})" if example.get("rule_id") else ""),
                f"  {example['explanation']}",
                "",
            ]
            for side in (left, right):
                source = side["source"]
                lines += [
                    f"    {side['statement']}",
                    f"      value    : {side['value_text']}"
                    + (
                        f"  -> {side['value_base']:,.4g} {side['unit']}"
                        if side["value_base"]
                        else ""
                    ),
                    f"      period   : {side['period_label'] or '-'}",
                    f"      source   : {source['document_title']}, page {source['page_number']}",
                    f'      evidence : "{side["evidence"]["quote"].strip()[:130]}"',
                    "",
                ]
            for reason in example.get("selected_because", []):
                lines.append(f"    chosen because {reason}")
            lines.append("")

    text = "\n".join(lines)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}")
    else:
        print(text)

    if missing:
        print(f"\n  {missing} of 4 cases are not present in the knowledge layer.")
    return 1 if missing else 0


def sample(size: int, seed: int, out: Path | None) -> int:
    """Write a worksheet of facts for manual precision checking."""
    with session_scope() as session:
        total = int(session.scalar(select(func.count(Fact.id))) or 0)
        if not total:
            print("Nothing has been ingested yet.")
            return 1

        facts = list(session.scalars(select(Fact)))
        pages = {page.id: page for page in session.scalars(select(Page))}
        titles = {
            document.id: document.title or document.filename
            for document in session.scalars(select(Document))
        }

        random.Random(seed).shuffle(facts)
        chosen = facts[:size]

        lines = [
            "# Precision sample",
            "",
            f"{len(chosen)} facts drawn at random from {total} (seed {seed}).",
            "",
            "Grounding pass rate measures whether the evidence checks out. It says nothing",
            "about whether a fact was attached to the right measure, period or scope — only",
            "reading the page can answer that. Mark each row and total the column.",
            "",
            "Verdict: `correct`, `wrong-measure`, `wrong-period`, `wrong-scope`,",
            "`wrong-value`, `not-a-fact`.",
            "",
        ]

        for index, fact in enumerate(chosen, start=1):
            page = pages.get(fact.page_id)
            match = "exact" if fact.evidence_exact else f"{fact.evidence_score:.0f}%"
            lines += [
                f"## {index}. {fact.statement}",
                "",
                f"- **Source**: {titles.get(fact.document_id, '?')}, "
                f"page {page.page_number if page else '?'}"
                + (f" (printed {page.printed_label})" if page and page.printed_label else ""),
                f"- **Subject / measure**: {fact.subject_surface} / {fact.predicate_surface}",
                f"- **Value**: {fact.value_text}"
                + (f"  → {fact.value_base:,.4g} {fact.unit_canonical}" if fact.value_base else ""),
                f"- **Period**: {fact.period_label or '—'}"
                + (f" ({fact.period_start} to {fact.period_end})" if fact.period_start else ""),
                f"- **Qualifiers**: {fact.qualifiers or '—'}   **Basis**: {fact.basis or '—'}",
                f"- **Confidence**: {fact.confidence:.2f}   **Evidence match**: {match}",
                "",
                f"> {fact.evidence_quote.strip()}",
                "",
                "**Verdict**: ",
                "",
            ]

    text = "\n".join(lines)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out} with {len(chosen)} facts to check")
    else:
        print(text)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("metrics", help="print what the pipeline recorded")
    subparsers.add_parser("audit", help="re-verify every fact against its source page")

    case_parser = subparsers.add_parser("cases", help="print the four required cases")
    case_parser.add_argument("--out", type=Path)

    sampler = subparsers.add_parser("sample", help="write a manual precision worksheet")
    sampler.add_argument("--size", type=int, default=40)
    sampler.add_argument("--seed", type=int, default=7)
    sampler.add_argument("--out", type=Path)

    parser.add_argument("--log-level", default="WARNING")
    arguments = parser.parse_args()
    configure_logging(arguments.log_level)

    if arguments.command == "metrics":
        return metrics()
    if arguments.command == "audit":
        return audit()
    if arguments.command == "cases":
        return cases(arguments.out)
    return sample(arguments.size, arguments.seed, arguments.out)


if __name__ == "__main__":
    raise SystemExit(main())
