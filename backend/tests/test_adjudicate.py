"""The second opinion.

An LLM asked to compare two things is influenced by which one it reads first. These tests
pin down what the pipeline does when that shows up: which verdict survives, what it is
worth afterwards, and what gets counted.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.config import Settings
from app.db.models import (
    REL_CONTRADICTS,
    REL_CORROBORATES,
    REL_RECONCILED,
    Fact,
)
from app.llm.base import LlmRequest, LlmResponse, Provider
from app.llm.client import LlmClient
from app.pipeline.adjudicate import (
    AdjudicationRequest,
    CrossCheckStats,
    adjudicate_all,
)

ALPHA = "the survey puts growth at 6.5%"
BETA = "the review puts growth at 8.2%"


class OrderSensitiveProvider(Provider):
    """Answers according to which fact it was shown first.

    A blunt stand-in for position bias, which in a real model is a tendency rather than a
    rule. The pipeline's behaviour has to be the same either way.
    """

    name = "order-sensitive"

    def __init__(self, first_alpha: dict, first_beta: dict) -> None:
        self._first_alpha = first_alpha
        self._first_beta = first_beta
        self.calls = 0

    def supports_vision(self) -> bool:
        return False

    def model_for(self, purpose: str, *, vision: bool = False) -> str:
        return "order-sensitive-model"

    async def complete(self, request: LlmRequest, model: str) -> LlmResponse:
        self.calls += 1
        alpha_at = request.user.find(ALPHA)
        beta_at = request.user.find(BETA)
        payload = self._first_alpha if alpha_at < beta_at else self._first_beta
        return LlmResponse(
            data=payload,
            raw_text=json.dumps(payload),
            provider=self.name,
            model=model,
            latency_ms=1,
        )


def verdict_payload(verdict: str, dimension: str = "none", confidence: float = 0.9) -> dict:
    return {
        "verdict": verdict,
        "dimension": dimension,
        "explanation": f"read as {verdict}.",
        "reconciliation": "",
        "confidence": confidence,
    }


def fact(fact_id: int, statement: str, value: float) -> Fact:
    return Fact(
        id=fact_id,
        document_id=fact_id,
        page_id=fact_id,
        measure_id=10,
        entity_id=5,
        statement=statement,
        subject_surface="India",
        predicate_surface="real gdp growth",
        value_base=value,
        value_text=f"{value}%",
        unit_class="ratio",
        period_label="2024-25",
        qualifiers={},
        confidence=0.9,
        evidence_score=100.0,
        evidence_quote=statement,
    )


def run(provider: OrderSensitiveProvider, tmp_path, *, cross_check: bool):
    left = fact(1, ALPHA, 6.5)
    right = fact(2, BETA, 8.2)
    item = AdjudicationRequest(
        left=left, right=right, note="values differ by 26%", delta_absolute=1.7, delta_relative=0.26
    )
    # Every prompt here is identical by construction, so a shared on-disk cache would serve
    # one test's answer to the next and quietly defeat the whole point of the swap.
    settings = Settings(data_dir=tmp_path, llm_cache_enabled=False)
    client = LlmClient(settings, provider=provider)
    stats = CrossCheckStats()
    results = asyncio.run(
        adjudicate_all(
            client,
            [item],
            {1: {"source": "survey", "context": ""}, 2: {"source": "review", "context": ""}},
            cross_check=cross_check,
            stats=stats,
        )
    )
    return results.get((1, 2)), stats


def test_a_verdict_that_survives_the_swap_is_kept(tmp_path):
    agreeing = verdict_payload("reconciled_by_context", "vintage", confidence=0.8)
    provider = OrderSensitiveProvider(agreeing, verdict_payload("reconciled_by_context", "vintage"))
    result, stats = run(provider, tmp_path, cross_check=True)

    assert result is not None
    assert result.relation_type == REL_RECONCILED
    assert result.order_sensitive is False
    assert stats.agreed == 1
    assert provider.calls == 2


def test_a_contradiction_that_does_not_survive_the_swap_is_not_asserted(tmp_path):
    """The strongest claim the system makes needs both readings to agree.

    Keeping the reconciliation leaves the pair visible and flagged rather than reported as
    a conflict on the strength of one ordering.
    """
    provider = OrderSensitiveProvider(
        verdict_payload("contradicts"),
        verdict_payload("reconciled_by_context", "vintage"),
    )
    result, stats = run(provider, tmp_path, cross_check=True)

    assert result is not None
    assert result.relation_type == REL_RECONCILED
    assert result.order_sensitive is True
    assert result.reverse_relation_type == REL_CONTRADICTS
    assert stats.order_sensitive == 1


def test_the_direction_of_the_disagreement_does_not_matter(tmp_path):
    """Same pair, same split decision, opposite orderings. Same outcome."""
    provider = OrderSensitiveProvider(
        verdict_payload("reconciled_by_context", "vintage"),
        verdict_payload("contradicts"),
    )
    result, _ = run(provider, tmp_path, cross_check=True)

    assert result is not None
    assert result.relation_type == REL_RECONCILED
    assert result.order_sensitive is True


def test_an_unsettled_verdict_is_worth_less_than_a_settled_one(tmp_path):
    provider = OrderSensitiveProvider(
        verdict_payload("corroborates", confidence=0.95),
        verdict_payload("reconciled_by_context", "period", confidence=0.95),
    )
    result, _ = run(provider, tmp_path, cross_check=True)

    assert result is not None
    assert result.confidence <= 0.55
    assert "opposite order" in result.explanation


def test_a_pair_one_ordering_calls_unrelated_is_dropped(tmp_path):
    """There is no version of "a relationship half the time" worth storing."""
    provider = OrderSensitiveProvider(
        verdict_payload("corroborates"),
        verdict_payload("unrelated"),
    )
    result, stats = run(provider, tmp_path, cross_check=True)

    assert result is None
    assert stats.dropped == 1


def test_cross_checking_off_asks_once_and_trusts_the_answer(tmp_path):
    provider = OrderSensitiveProvider(
        verdict_payload("contradicts"),
        verdict_payload("reconciled_by_context", "vintage"),
    )
    result, stats = run(provider, tmp_path, cross_check=False)

    assert result is not None
    assert result.relation_type == REL_CONTRADICTS
    assert provider.calls == 1
    assert stats.checked == 0


@pytest.mark.parametrize(
    "shared",
    [
        verdict_payload("corroborates"),
        verdict_payload("contradicts"),
    ],
)
def test_a_model_that_is_not_order_sensitive_pays_only_the_second_call(shared, tmp_path):
    provider = OrderSensitiveProvider(shared, shared)
    result, stats = run(provider, tmp_path, cross_check=True)

    assert result is not None
    assert result.relation_type in {REL_CORROBORATES, REL_CONTRADICTS}
    assert result.order_sensitive is False
    assert stats.as_dict()["agreement_rate"] == 1.0
