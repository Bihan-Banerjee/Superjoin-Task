"""The deterministic reconciler.

Given two facts that the candidate stage thinks are about the same thing, decide how they
relate — or decline, and hand the pair to the model.

Rules run before the model for three reasons. They are free, so the system can compare far
more pairs than a model budget would allow. They are reproducible, so the same corpus
always yields the same relationships. And they are explainable in a way that matters here:
"these differ because one is stated in crore and the other in millions" is a better answer
than a paragraph of prose asserting the same thing, because it names a checkable reason.

The escalation policy is the interesting part. A pair is only sent to the model when the
rules cannot decide *and* the answer would change something — chiefly when values disagree
and no mechanical explanation accounts for it. A confident rule verdict is never
second-guessed by a model call, because that would spend budget to make the system less
predictable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core import periods as period_lib
from app.core.units import relative_difference, rounding_tolerance
from app.db.models import (
    DECIDED_BY_RULE,
    DIM_BASIS,
    DIM_CURRENCY,
    DIM_DEFINITION,
    DIM_NONE,
    DIM_PERIOD,
    DIM_SEGMENT,
    DIM_UNIT_SCALE,
    DIM_VINTAGE,
    REL_CONTRADICTS,
    REL_CORROBORATES,
    REL_RECONCILED,
    REL_REFINES,
    REL_SUPERSEDES,
    Fact,
)

# Qualifier keys that change what a value measures. Two facts differing on one of these are
# reconciled by that difference rather than being in conflict.
DISCRIMINATING_QUALIFIERS = (
    "segment",
    "scope",
    "geography",
    "definition",
    "counterparty",
    "product",
    "channel",
    "category",
    "sector",
    "instrument",
    "maturity",
    "tenor",
)

# A basis pairing that explains a difference on its own. An estimate and an outcome for the
# same period are not in disagreement; they are two different statements.
_FORWARD_LOOKING = {"estimate", "projection", "forecast", "budgeted", "target", "provisional"}
_BACKWARD_LOOKING = {"actual", "revised", "restated"}

# How far along the reporting cycle each basis sits. A higher rank replaces a lower one for
# the same period: an outcome settles what a projection guessed at, and a restatement
# replaces the figure it restates.
#
# Only bases with a genuine ordering appear here. A projection and an estimate are two
# readings made at different removes from the event, not successive corrections of one
# another, so they share a rank and stay unordered. Pro forma is absent entirely: it is a
# different basis of preparation rather than a later view of the same one. Anything
# unranked falls through to the existing reconciliation, which reports the difference
# without claiming one side is stale.
_SUPERSESSION_RANK = {
    "projection": 0,
    "forecast": 0,
    "budgeted": 0,
    "target": 0,
    "estimate": 1,
    "provisional": 1,
    "actual": 2,
    "revised": 3,
    "restated": 3,
}


@dataclass
class Verdict:
    relation_type: str
    dimension: str = DIM_NONE
    subtype: str | None = None
    explanation: str = ""
    confidence: float = 0.8
    rule_id: str | None = None
    severity: float = 0.0
    delta_absolute: float | None = None
    delta_relative: float | None = None
    decided_by: str = DECIDED_BY_RULE
    escalate: bool = False
    note: str = ""
    # Which of the two facts the other one replaces, named by id rather than by side.
    # Relations are stored with their pair in a fixed id order so the uniqueness constraint
    # catches a duplicate whichever fact arrived first, which means "left" and "right" here
    # need not survive into the row. An id does.
    superseded_fact_id: int | None = None
    # Set when the same pair was adjudicated in both orders and the model did not give the
    # same answer twice. Recorded rather than hidden: a verdict that depends on which fact
    # was presented first is a weaker claim than one that does not, and the difference is
    # worth being able to count.
    order_sensitive: bool = False
    reverse_relation_type: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "relation_type": self.relation_type,
            "dimension": self.dimension,
            "subtype": self.subtype,
            "explanation": self.explanation,
            "confidence": self.confidence,
            "rule_id": self.rule_id,
            "severity": self.severity,
        }


ESCALATE = Verdict(relation_type="", escalate=True)
DROP = Verdict(relation_type="", rule_id="not-comparable")


def reconcile(left: Fact, right: Fact) -> Verdict:
    """Classify a pair of facts, or ask for it to be escalated."""
    if left.measure_id is None or right.measure_id is None:
        return _escalate("neither fact resolved to a canonical measure")
    if left.measure_id != right.measure_id:
        return DROP
    both_entities_known = left.entity_id is not None and right.entity_id is not None
    if both_entities_known and left.entity_id != right.entity_id:
        return DROP

    period_relation = _period_relation(left, right)
    values_present = left.value_base is not None and right.value_base is not None

    # Non-numeric facts have nothing to compare arithmetically, so agreement is a question
    # about language and belongs with the model.
    if not values_present:
        return _reconcile_without_values(left, right, period_relation)

    if not _units_comparable(left, right):
        return _unit_mismatch(left, right)

    difference = relative_difference(left.value_base, right.value_base)
    tolerance = rounding_tolerance(left.value_base, right.value_base, left.unit_class)
    agrees = difference <= tolerance
    absolute = abs(left.value_base - right.value_base)

    if period_relation == period_lib.OVERLAP_UNKNOWN:
        return _escalate(
            "the reporting period of at least one fact could not be resolved to dates",
            delta_absolute=absolute,
            delta_relative=difference,
        )

    if period_relation in {period_lib.OVERLAP_CONTAINS, period_lib.OVERLAP_CONTAINED}:
        return Verdict(
            relation_type=REL_REFINES,
            dimension=DIM_PERIOD,
            subtype="sub_period",
            rule_id="period-containment",
            explanation=(
                f"{_period_text(left)} and {_period_text(right)} are not the same span: one "
                "falls inside the other, so the two figures cover different amounts of time."
            ),
            confidence=0.9,
            delta_absolute=absolute,
            delta_relative=difference,
        )

    if period_relation in {period_lib.OVERLAP_DISJOINT, period_lib.OVERLAP_PARTIAL}:
        return Verdict(
            relation_type=REL_RECONCILED,
            dimension=DIM_PERIOD,
            subtype="different_period",
            rule_id="period-differs",
            explanation=(
                f"These cover different periods — {_period_text(left)} against "
                f"{_period_text(right)} — so the values are not expected to match."
            ),
            confidence=0.92,
            delta_absolute=absolute,
            delta_relative=difference,
        )

    # From here the periods are identical, so any difference needs another explanation.
    qualifier_difference = _qualifier_difference(left, right)
    if qualifier_difference is not None:
        key, left_value, right_value = qualifier_difference
        return Verdict(
            relation_type=REL_RECONCILED,
            dimension=DIM_SEGMENT if key == "segment" else _dimension_for(key),
            subtype=f"{key}_differs",
            rule_id="qualifier-differs",
            explanation=(
                f"Both cover {_period_text(left)}, but they are scoped differently: "
                f"{key} is {left_value!r} for one and {right_value!r} for the other."
            ),
            confidence=0.88,
            delta_absolute=absolute,
            delta_relative=difference,
        )

    basis_difference = _basis_difference(left, right)
    if basis_difference is not None and not agrees:
        superseded = _supersession(left, right)
        if superseded is not None:
            stale, current = superseded
            return Verdict(
                relation_type=REL_SUPERSEDES,
                dimension=DIM_VINTAGE,
                subtype="later_statement",
                rule_id="basis-supersedes",
                explanation=(
                    f"Both cover {_period_text(left)}. {current.value_text} is stated as "
                    f"{current.basis}, which replaces the {stale.basis} of "
                    f"{stale.value_text} rather than disagreeing with it."
                ),
                confidence=0.88,
                delta_absolute=absolute,
                delta_relative=difference,
                superseded_fact_id=stale.id,
            )
        return Verdict(
            relation_type=REL_RECONCILED,
            dimension=DIM_VINTAGE if basis_difference[2] else DIM_BASIS,
            subtype="basis_differs",
            rule_id="basis-differs",
            explanation=(
                f"Both cover {_period_text(left)}, but one is stated as "
                f"{basis_difference[0]} and the other as {basis_difference[1]}, so they are "
                "not two measurements of the same thing."
            ),
            confidence=0.87,
            delta_absolute=absolute,
            delta_relative=difference,
        )

    if agrees:
        scale_differs = (left.unit_scale or 1) != (right.unit_scale or 1)
        explanation = _agreement_text(left, right, difference, scale_differs)
        dimension = DIM_UNIT_SCALE if scale_differs else DIM_NONE
        subtype = (
            "scale_equivalent"
            if scale_differs
            else ("exact" if difference == 0 else "within_rounding")
        )
        if basis_difference is not None:
            # Agreeing figures on different bases still corroborate, but silently calling
            # a projection and an outcome "the same figure" hides the more interesting
            # fact, which is that the projection turned out to be right.
            dimension = DIM_VINTAGE if basis_difference[2] else DIM_BASIS
            subtype = "agrees_across_basis"
            explanation = (
                f"{explanation} They are stated on different bases — {basis_difference[0]} "
                f"and {basis_difference[1]} — and still agree."
            )
        return Verdict(
            relation_type=REL_CORROBORATES,
            dimension=dimension,
            subtype=subtype,
            rule_id="values-agree",
            explanation=explanation,
            confidence=0.95 if difference == 0 else 0.9,
            delta_absolute=absolute,
            delta_relative=difference,
        )

    # Same measure, same entity, same period, same units, no qualifier or basis difference,
    # and the numbers disagree. That is as close to a real contradiction as rules can get,
    # but the evidence may still carry a distinction the extractor did not capture, so a
    # large gap is put to the model rather than asserted.
    severity = severity_for(left, right, difference)
    if difference >= 0.5 or left.confidence < 0.7 or right.confidence < 0.7:
        return _escalate(
            f"same measure, entity and period, but the values differ by {difference:.1%}",
            delta_absolute=absolute,
            delta_relative=difference,
        )

    return Verdict(
        relation_type=REL_CONTRADICTS,
        dimension=DIM_NONE,
        subtype="value_conflict",
        rule_id="values-disagree",
        explanation=(
            f"Both state {left.predicate_surface} for {_period_text(left)} on the same "
            f"basis, but the values are {left.value_text} and {right.value_text}, a "
            f"difference of {difference:.1%}."
        ),
        confidence=0.82,
        severity=severity,
        delta_absolute=absolute,
        delta_relative=difference,
    )


def _reconcile_without_values(left: Fact, right: Fact, period_relation: str) -> Verdict:
    if period_relation in {period_lib.OVERLAP_DISJOINT, period_lib.OVERLAP_PARTIAL}:
        return Verdict(
            relation_type=REL_RECONCILED,
            dimension=DIM_PERIOD,
            subtype="different_period",
            rule_id="period-differs-textual",
            explanation=(
                f"These describe different periods, {_period_text(left)} against "
                f"{_period_text(right)}."
            ),
            confidence=0.8,
        )
    return _escalate("both facts are textual, so agreement cannot be decided arithmetically")


def _unit_mismatch(left: Fact, right: Fact) -> Verdict:
    if left.currency and right.currency and left.currency != right.currency:
        return Verdict(
            relation_type=REL_RECONCILED,
            dimension=DIM_CURRENCY,
            subtype="different_currency",
            rule_id="currency-differs",
            explanation=(
                f"One figure is in {left.currency} and the other in {right.currency}. No "
                "exchange rate is applied, because converting would introduce a number "
                "neither document states."
            ),
            confidence=0.9,
        )
    return Verdict(
        relation_type=REL_RECONCILED,
        dimension=DIM_DEFINITION,
        subtype="different_unit_class",
        rule_id="unit-class-differs",
        explanation=(
            f"These are measured in different kinds of unit — {left.unit_canonical or 'unknown'} "
            f"against {right.unit_canonical or 'unknown'} — so they are not the same quantity."
        ),
        confidence=0.85,
    )


def _escalate(
    note: str, *, delta_absolute: float | None = None, delta_relative: float | None = None
) -> Verdict:
    return Verdict(
        relation_type="",
        escalate=True,
        note=note,
        delta_absolute=delta_absolute,
        delta_relative=delta_relative,
    )


def _period_relation(left: Fact, right: Fact) -> str:
    return period_lib.relate(_period_of(left), _period_of(right))


def _period_of(fact: Fact) -> period_lib.Period:
    from datetime import date

    if not fact.period_start or not fact.period_end:
        return period_lib.Period(
            label=fact.period_label or "", kind=period_lib.UNKNOWN, start=None, end=None
        )
    return period_lib.Period(
        label=fact.period_label or "",
        kind=fact.period_kind or period_lib.UNKNOWN,
        start=date.fromisoformat(fact.period_start),
        end=date.fromisoformat(fact.period_end),
    )


def _period_text(fact: Fact) -> str:
    if fact.period_label:
        return fact.period_label
    if fact.period_start and fact.period_end:
        return f"{fact.period_start} to {fact.period_end}"
    return "an unstated period"


def _units_comparable(left: Fact, right: Fact) -> bool:
    if not left.unit_class or not right.unit_class:
        return left.unit_class == right.unit_class
    if left.unit_class != right.unit_class:
        return False
    return (left.currency or None) == (right.currency or None)


def _qualifier_difference(left: Fact, right: Fact) -> tuple[str, str, str] | None:
    """First discriminating qualifier the two facts disagree on.

    A qualifier present on one side and absent on the other counts: a segment figure and a
    total are different claims, and treating the absent side as a wildcard would report
    them as contradicting.
    """
    left_qualifiers = {key: str(value) for key, value in (left.qualifiers or {}).items()}
    right_qualifiers = {key: str(value) for key, value in (right.qualifiers or {}).items()}

    for key in DISCRIMINATING_QUALIFIERS:
        left_value = left_qualifiers.get(key)
        right_value = right_qualifiers.get(key)
        if left_value is None and right_value is None:
            continue
        if (left_value or "").strip().lower() != (right_value or "").strip().lower():
            return key, left_value or "not stated", right_value or "not stated"
    return None


def _basis_difference(left: Fact, right: Fact) -> tuple[str, str, bool] | None:
    """Differing bases, and whether the difference is one of data vintage."""
    left_basis = (left.basis or "").strip().lower()
    right_basis = (right.basis or "").strip().lower()
    if not left_basis and not right_basis:
        return None
    if left_basis == right_basis:
        return None

    left_label = left_basis or "not stated"
    right_label = right_basis or "not stated"
    vintage = bool(
        ({left_basis} & _FORWARD_LOOKING and {right_basis} & _BACKWARD_LOOKING)
        or ({right_basis} & _FORWARD_LOOKING and {left_basis} & _BACKWARD_LOOKING)
        or {left_basis, right_basis} & {"revised", "restated"}
    )
    return left_label, right_label, vintage


def _supersession(left: Fact, right: Fact) -> tuple[Fact, Fact] | None:
    """The (superseded, superseding) pair when one basis replaces the other.

    Reporting the same period twice on different bases is not a conflict, and calling it
    one merely by dimension leaves out the part a reader wants: which figure is the current
    one. An outcome settles an estimate; a restatement replaces what it restates.

    The ordering comes from the bases alone. Publication dates are deliberately not used:
    a later document repeating an earlier figure unchanged is not a restatement, and a
    document that disagrees without saying it is revising anything is a contradiction — the
    interesting kind, and not one to quietly relabel as an update.
    """
    left_rank = _SUPERSESSION_RANK.get((left.basis or "").strip().lower())
    right_rank = _SUPERSESSION_RANK.get((right.basis or "").strip().lower())
    if left_rank is None or right_rank is None or left_rank == right_rank:
        return None
    return (left, right) if left_rank < right_rank else (right, left)


def _dimension_for(key: str) -> str:
    from app.db.models import DIM_SCOPE

    return {"definition": DIM_DEFINITION}.get(key, DIM_SCOPE)


def _agreement_text(left: Fact, right: Fact, difference: float, scale_differs: bool) -> str:
    if scale_differs:
        return (
            f"Both report {left.value_text} and {right.value_text} for {_period_text(left)}. "
            "Written at different scales, these are the same amount."
        )
    if difference == 0:
        return f"Both state {left.value_text} for {_period_text(left)}."
    return (
        f"Both state {left.value_text} and {right.value_text} for {_period_text(left)}, "
        "which agree to the precision each is reported at."
    )


def severity_for(left: Fact, right: Fact, difference: float) -> float:
    """How much a contradiction should be trusted and how much it matters.

    Weighted by the size of the gap and by how well grounded each side is, so a large
    disagreement between two exactly-quoted facts outranks a small one between two the
    extractor was unsure about.
    """
    magnitude = min(difference, 1.0)
    grounding = (left.evidence_score + right.evidence_score) / 200
    certainty = (left.confidence + right.confidence) / 2
    cross_document = 1.0 if left.document_id != right.document_id else 0.75
    return round(
        min(1.0, 0.55 * magnitude + 0.2 * grounding + 0.25 * certainty) * cross_document, 3
    )
