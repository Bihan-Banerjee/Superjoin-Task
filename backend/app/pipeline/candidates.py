"""Candidate pair generation.

Comparing every fact with every other fact is quadratic, and at a few thousand facts that
is already millions of comparisons for a handful of real relationships. Worse, most of
those pairs are not merely wasted work — they are opportunities to produce a wrong
relationship between two things that were never related.

So pairs are proposed by three independent routes and unioned:

* **Same canonical measure.** The strongest signal there is, and the reason the registry
  exists. Two facts assigned to the same measure are worth comparing whatever their wording.
* **Vector neighbourhood.** Catches facts whose measures were kept separate but which are
  clearly about the same thing, which is where a conservative registry costs recall.
* **Lexical overlap.** Catches the cases embeddings miss, typically a rare proper noun or
  an identifier that carries the meaning.

Only new facts are paired against the corpus. Adding a document therefore costs work
proportional to that document, not to everything ingested so far, which is what makes
incremental ingest possible without rebuilding.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import Fact, FactEmbedding
from app.pipeline import embed

logger = logging.getLogger(__name__)

VECTOR_NEIGHBOURS = 12
VECTOR_THRESHOLD = 0.82
LEXICAL_NEIGHBOURS = 8
MAX_CANDIDATES_PER_FACT = 40

_FTS_UNSAFE = re.compile(r"[^\w\s]")


@dataclass(frozen=True)
class Pair:
    left_id: int
    right_id: int
    similarity: float
    source: str

    def key(self) -> tuple[int, int]:
        return (
            (self.left_id, self.right_id)
            if self.left_id < self.right_id
            else (
                self.right_id,
                self.left_id,
            )
        )


def generate(session: Session, new_fact_ids: list[int]) -> list[Pair]:
    if not new_fact_ids:
        return []

    new_facts = list(session.scalars(select(Fact).where(Fact.id.in_(new_fact_ids))))
    pairs: dict[tuple[int, int], Pair] = {}

    for pair in _by_measure(session, new_facts):
        pairs.setdefault(pair.key(), pair)
    for pair in _by_vector(session, new_facts):
        pairs.setdefault(pair.key(), pair)
    for pair in _by_lexical(session, new_facts):
        pairs.setdefault(pair.key(), pair)

    return _cap_per_fact(list(pairs.values()))


def _by_measure(session: Session, new_facts: list[Fact]) -> list[Pair]:
    measure_ids = {fact.measure_id for fact in new_facts if fact.measure_id is not None}
    if not measure_ids:
        return []

    rows = list(session.scalars(select(Fact).where(Fact.measure_id.in_(measure_ids))))
    by_measure: dict[int, list[Fact]] = {}
    for fact in rows:
        by_measure.setdefault(fact.measure_id, []).append(fact)

    new_ids = {fact.id for fact in new_facts}
    pairs: list[Pair] = []
    for fact in new_facts:
        if fact.measure_id is None:
            continue
        for other in by_measure.get(fact.measure_id, []):
            if other.id == fact.id:
                continue
            # Both sides new: compare each unordered pair once.
            if other.id in new_ids and other.id < fact.id:
                continue
            if _same_place(fact, other):
                continue
            pairs.append(Pair(fact.id, other.id, similarity=1.0, source="measure"))
    return pairs


def _by_vector(session: Session, new_facts: list[Fact]) -> list[Pair]:
    stored = list(session.execute(select(FactEmbedding.fact_id, FactEmbedding.vector)))
    if not stored:
        return []

    dims = embed.dimensions()
    ids = [row[0] for row in stored]
    matrix = embed.stack([row[1] for row in stored], dims)
    position = {fact_id: index for index, fact_id in enumerate(ids)}

    facts_by_id = {fact.id: fact for fact in session.scalars(select(Fact).where(Fact.id.in_(ids)))}

    new_ids = {fact.id for fact in new_facts}
    pairs: list[Pair] = []
    for fact in new_facts:
        index = position.get(fact.id)
        if index is None:
            continue
        query = matrix[index]
        for neighbour_index, score in embed.top_matches(
            query, matrix, limit=VECTOR_NEIGHBOURS + 1, threshold=VECTOR_THRESHOLD
        ):
            other_id = ids[neighbour_index]
            if other_id == fact.id:
                continue
            if other_id in new_ids and other_id < fact.id:
                continue
            other = facts_by_id.get(other_id)
            if other is None or _same_place(fact, other):
                continue
            pairs.append(Pair(fact.id, other_id, similarity=float(score), source="vector"))
    return pairs


def _by_lexical(session: Session, new_facts: list[Fact]) -> list[Pair]:
    """Full-text neighbours, for the overlap embeddings do not capture."""
    pairs: list[Pair] = []
    new_ids = {fact.id for fact in new_facts}

    for fact in new_facts:
        query = _fts_query(f"{fact.subject_surface} {fact.predicate_surface}")
        if not query:
            continue
        try:
            rows = session.execute(
                text(
                    "SELECT rowid, bm25(facts_fts) AS score FROM facts_fts "
                    "WHERE facts_fts MATCH :query ORDER BY score LIMIT :limit"
                ),
                {"query": query, "limit": LEXICAL_NEIGHBOURS + 1},
            ).all()
        except Exception as error:  # a malformed FTS expression must not stop ingest
            logger.debug("lexical candidate query failed for fact %s: %s", fact.id, error)
            continue

        for other_id, _score in rows:
            if other_id == fact.id or (other_id in new_ids and other_id < fact.id):
                continue
            pairs.append(Pair(fact.id, int(other_id), similarity=0.0, source="lexical"))
    return pairs


def _fts_query(text_value: str) -> str:
    """Build an OR query of the distinctive tokens, quoted so FTS5 cannot misparse them."""
    tokens = [
        token
        for token in _FTS_UNSAFE.sub(" ", text_value.lower()).split()
        if len(token) > 2 and token not in _STOPWORDS
    ]
    if not tokens:
        return ""
    distinct = list(dict.fromkeys(tokens))[:8]
    return " OR ".join(f'"{token}"' for token in distinct)


_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "its",
    "was",
    "were",
    "has",
    "had",
    "are",
    "per",
    "total",
    "limited",
    "ltd",
}


def _same_place(left: Fact, right: Fact) -> bool:
    """Facts from the same page are usually one statement read twice.

    Comparing them produces relationships that say nothing — a table row against its own
    total — while burning the comparison budget the cross-document pairs need.
    """
    return left.document_id == right.document_id and left.page_id == right.page_id


def _cap_per_fact(pairs: list[Pair]) -> list[Pair]:
    """Bound the fan-out of any single fact.

    A measure such as "total revenue" accumulates hundreds of facts across a corpus, and
    without a cap one popular measure would dominate every ingest. Cross-document pairs are
    kept first because they are what the assignment is actually about.
    """
    ordered = sorted(
        pairs,
        key=lambda pair: (-pair.similarity, pair.source != "measure"),
    )
    counts: dict[int, int] = {}
    kept: list[Pair] = []
    for pair in ordered:
        left_count = counts.get(pair.left_id, 0)
        right_count = counts.get(pair.right_id, 0)
        if left_count >= MAX_CANDIDATES_PER_FACT or right_count >= MAX_CANDIDATES_PER_FACT:
            continue
        counts[pair.left_id] = left_count + 1
        counts[pair.right_id] = right_count + 1
        kept.append(pair)
    return kept


def store_embeddings(session: Session, facts: list[Fact]) -> None:
    if not facts:
        return
    vectors = embed.embed_texts([fact.claim_key or fact.statement for fact in facts])
    dims = int(vectors.shape[1]) if vectors.size else embed.dimensions()
    for fact, vector in zip(facts, vectors, strict=False):
        session.merge(
            FactEmbedding(
                fact_id=fact.id,
                dimensions=dims,
                vector=embed.to_blob(np.asarray(vector, dtype=np.float32)),
            )
        )
