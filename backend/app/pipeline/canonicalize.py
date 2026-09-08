"""The measure and entity registries.

This is where the schema grows. Nothing in the code enumerates what can be measured. A
document introduces a phrase, the registry either recognises it as something already known
or admits it as a new canonical measure, and from then on facts using either wording are
comparable.

Resolution runs cheapest-first and only escalates when it has to:

1. An exact alias match. Free, and covers the common case of a document repeating itself.
2. Nearest neighbour on a local embedding. Free, and covers obvious rewordings such as
   "service revenue" against "revenue from services".
3. A model call, batched, only for the band in between where similarity is suggestive but
   not decisive.

Two guards keep the registry from collapsing:

* Measures of different unit classes never merge. "Revenue" is an amount and "revenue
  growth" is a rate; a model asked in isolation will sometimes merge them, and the result
  is a system that reports a contradiction between 8,142 and 13.

* When uncertain, create. A registry with two rows for one measure loses some links. A
  registry with one row for two measures manufactures contradictions between numbers that
  were never the same number, which is a far worse failure for this system to have.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Entity, Measure, QualifierKey
from app.llm.base import PURPOSE_REGISTRY, LlmError, LlmRequest
from app.llm.client import LlmClient
from app.llm.prompts import MEASURE_LINKING_SYSTEM, measure_linking_prompt
from app.llm.schemas import MEASURE_LINKING_SCHEMA
from app.pipeline import embed

logger = logging.getLogger(__name__)

# Above this, an embedding match is treated as the same measure without asking.
LINK_THRESHOLD = 0.94
# Below this, it is treated as a different measure without asking.
CREATE_THRESHOLD = 0.80
# Entities tolerate more surface variation ("Delhivery Ltd", "Delhivery Limited").
ENTITY_LINK_THRESHOLD = 0.92
ENTITY_CREATE_THRESHOLD = 0.78

MAX_ALIASES = 40
LLM_BATCH_SIZE = 24

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_LEADING_ARTICLE = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)
_CORPORATE_SUFFIX = re.compile(
    r"\b(limited|ltd|private|pvt|incorporated|inc|corporation|corp|plc|llp|company|co)\b\.?",
    re.IGNORECASE,
)


def slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return _SLUG_STRIP.sub("-", ascii_text.lower()).strip("-") or "unnamed"


def alias_key(text: str) -> str:
    """Comparison form for an alias: lowercase, article-stripped, punctuation-free."""
    cleaned = _LEADING_ARTICLE.sub("", str(text).strip())
    ascii_text = unicodedata.normalize("NFKD", cleaned).encode("ascii", "ignore").decode("ascii")
    return " ".join(_SLUG_STRIP.sub(" ", ascii_text.lower()).split())


def entity_alias_key(text: str) -> str:
    """Alias form for organisations, with legal-form suffixes removed.

    "Delhivery Limited" and "Delhivery Ltd" are the same company written two ways, and a
    document will use both. Stripping the suffix resolves them without a model call.
    """
    stripped = _CORPORATE_SUFFIX.sub(" ", alias_key(text))
    return " ".join(stripped.split()) or alias_key(text)


def _same_organisation(left: str, right: str) -> bool:
    """Whether two names can be the same organisation, ignoring legal form.

    An embedding score is not sufficient on its own here, and relying on it did real damage:
    "Delhivery Limited" and "Delhivery Corp Limited, United Kingdom" score highly and are
    two different legal entities, so the subsidiary absorbed 245 facts belonging to the
    parent. Facts about different entities must never be compared, which makes a wrong merge
    much worse than a missed one.

    So a merge additionally requires the same tokens once legal forms and articles are
    stripped. That still resolves "Delhivery Ltd" against "Delhivery Limited", and refuses
    anything carrying a distinguishing word the other lacks.
    """
    return set(entity_alias_key(left).split()) == set(entity_alias_key(right).split())


@dataclass
class _Candidate:
    surface: str
    unit_class: str | None
    example: str


@dataclass
class RegistryReport:
    measures_created: int = 0
    measures_linked: int = 0
    entities_created: int = 0
    entities_linked: int = 0
    qualifier_keys_seen: set[str] = field(default_factory=set)
    escalated_to_model: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "measures_created": self.measures_created,
            "measures_linked": self.measures_linked,
            "entities_created": self.entities_created,
            "entities_linked": self.entities_linked,
            "qualifier_keys": sorted(self.qualifier_keys_seen),
            "escalated_to_model": self.escalated_to_model,
        }


class MeasureRegistry:
    """Resolves measure surfaces, embedding in batches and keeping the matrix incrementally.

    The obvious implementation — embed each surface as it comes up, and stack the registry's
    vectors to compare against — is quadratic and dominated by per-call overhead. On a single
    hundred-page filing it took longer than the extraction it was resolving. Embedding every
    surface in one pass and appending to a kept matrix makes the stage proportional to the
    number of *distinct* measures rather than to the number of facts.
    """

    def __init__(self, session: Session, *, document_id: int | None = None) -> None:
        self._session = session
        self._document_id = document_id
        self._measures: list[Measure] = list(session.scalars(select(Measure)))
        self._by_alias: dict[str, Measure] = {}
        for measure in self._measures:
            for alias in [measure.name, *(measure.aliases or [])]:
                self._by_alias.setdefault(alias_key(alias), measure)

        dimensions = embed.dimensions()
        self._vectors: list[np.ndarray] = [
            embed.from_blob(measure.embedding, dimensions)
            if measure.embedding
            else np.zeros(dimensions, dtype=np.float32)
            for measure in self._measures
        ]
        self.report = RegistryReport()

    def lookup_exact(self, surface: str) -> Measure | None:
        return self._by_alias.get(alias_key(surface))

    async def resolve_batch(
        self,
        candidates: list[tuple[str, str | None, str]],
        client: LlmClient | None = None,
    ) -> dict[str, Measure]:
        """Resolve many surfaces at once, escalating only the ambiguous ones."""
        resolved: dict[str, Measure] = {}
        ambiguous: list[_Candidate] = []

        pending: list[tuple[str, str | None, str]] = []
        for surface, unit_class, example in candidates:
            if not surface.strip() or surface in resolved:
                continue
            existing = self.lookup_exact(surface)
            if existing is not None:
                resolved[surface] = existing
                self.report.measures_linked += 1
            else:
                pending.append((surface, unit_class, example))

        if not pending:
            return resolved

        # One inference for every surface that needs one, rather than one per surface.
        queries = embed.embed_texts([surface for surface, _, _ in pending])

        for (surface, unit_class, example), query in zip(pending, queries, strict=False):
            # Re-checked inside the loop: an earlier candidate in this same batch may have
            # created the measure this one should link to.
            existing = self.lookup_exact(surface)
            if existing is not None:
                resolved[surface] = existing
                self.report.measures_linked += 1
                continue

            match, score = self._nearest(query, unit_class)
            if match is not None and score >= LINK_THRESHOLD:
                self._add_alias(match, surface)
                resolved[surface] = match
                self.report.measures_linked += 1
            elif match is None or score <= CREATE_THRESHOLD:
                resolved[surface] = self._create(surface, unit_class, example, vector=query)
            else:
                ambiguous.append(_Candidate(surface, unit_class, example))

        if ambiguous:
            resolved.update(await self._resolve_with_model(ambiguous, client))
        return resolved

    def _nearest(self, query: np.ndarray, unit_class: str | None) -> tuple[Measure | None, float]:
        """Closest registered measure of a compatible unit class."""
        eligible = [
            index
            for index, measure in enumerate(self._measures)
            if _classes_compatible(measure.unit_class, unit_class)
        ]
        if not eligible:
            return None, 0.0

        matrix = np.vstack([self._vectors[index] for index in eligible])
        matches = embed.top_matches(query, matrix, limit=1, threshold=0.0)
        if not matches:
            return None, 0.0
        position, score = matches[0]
        return self._measures[eligible[position]], score

    async def _resolve_with_model(
        self, candidates: list[_Candidate], client: LlmClient | None
    ) -> dict[str, Measure]:
        if client is None:
            # Without a model the conservative choice is to create, for the reason given at
            # the top of this module: a wrong merge is worse than a missed one.
            return self._create_all([(item, item.surface) for item in candidates])

        resolved: dict[str, Measure] = {}
        for start in range(0, len(candidates), LLM_BATCH_SIZE):
            batch = candidates[start : start + LLM_BATCH_SIZE]
            self.report.escalated_to_model += len(batch)
            decisions = await self._ask(batch, client)

            pending: list[tuple[_Candidate, str]] = []
            for item in batch:
                decision = decisions.get(alias_key(item.surface))
                target = None
                if decision and decision.get("action") == "link":
                    target = self.lookup_exact(str(decision.get("measure", "")))
                    if target is not None and not _classes_compatible(
                        target.unit_class, item.unit_class
                    ):
                        target = None
                if target is not None:
                    self._add_alias(target, item.surface)
                    resolved[item.surface] = target
                    self.report.measures_linked += 1
                else:
                    name = item.surface
                    if decision and decision.get("action") == "create":
                        name = str(decision.get("measure") or item.surface)
                    pending.append((item, name))

            resolved.update(self._create_all(pending))
        return resolved

    def _create_all(self, pending: list[tuple[_Candidate, str]]) -> dict[str, Measure]:
        """Create several measures, embedding their names in one pass.

        The model often answers with a canonical name that differs from the surface, and
        creating one at a time meant an inference per measure. On a document that escalates
        a few hundred candidates that was the slowest thing in the pipeline.
        """
        if not pending:
            return {}
        names = [name.strip().lower() for _, name in pending]
        vectors = embed.embed_texts(names)
        return {
            item.surface: self._create(
                item.surface,
                item.unit_class,
                item.example,
                canonical_name=name,
                vector=vector,
                vector_is_for_name=True,
            )
            for (item, name), vector in zip(pending, vectors, strict=False)
        }

    async def _ask(self, batch: list[_Candidate], client: LlmClient) -> dict[str, dict[str, Any]]:
        existing = [
            {
                "name": measure.name,
                "unit_class": measure.unit_class,
                "aliases": list(measure.aliases or []),
            }
            for measure in self._measures
        ][:120]
        payload = [
            {"surface": item.surface, "unit_class": item.unit_class, "example": item.example}
            for item in batch
        ]
        request = LlmRequest(
            purpose=PURPOSE_REGISTRY,
            system=MEASURE_LINKING_SYSTEM,
            user=measure_linking_prompt(payload, existing),
            schema=MEASURE_LINKING_SCHEMA,
            max_output_tokens=4096,
        )
        try:
            response = await client.complete(request)
        except LlmError as error:
            logger.warning("measure linking call failed, creating separately: %s", error)
            return {}

        decisions: dict[str, dict[str, Any]] = {}
        for entry in (response.data or {}).get("decisions", []):
            surface = str(entry.get("surface", "")).strip()
            if surface:
                decisions[alias_key(surface)] = entry
        return decisions

    def _create(
        self,
        surface: str,
        unit_class: str | None,
        example: str,
        *,
        canonical_name: str | None = None,
        vector: np.ndarray | None = None,
        vector_is_for_name: bool = False,
    ) -> Measure:
        name = (canonical_name or surface).strip().lower()
        # A caller that already embedded the canonical name says so; otherwise the vector it
        # supplied is for the surface, and is only reusable when the two are the same text.
        if vector is None or not (vector_is_for_name or name == surface.strip().lower()):
            vector = embed.embed_text(name)

        measure = Measure(
            slug=_unique_slug(self._session, Measure, slugify(name)),
            name=name,
            description=example[:500] or None,
            unit_class=unit_class,
            aliases=sorted({surface, name}),
            embedding=embed.to_blob(vector),
            first_seen_document_id=self._document_id,
        )
        self._session.add(measure)
        self._session.flush()
        self._measures.append(measure)
        self._vectors.append(vector)
        self._by_alias[alias_key(name)] = measure
        self._by_alias[alias_key(surface)] = measure
        self.report.measures_created += 1
        return measure

    def _add_alias(self, measure: Measure, surface: str) -> None:
        aliases = list(measure.aliases or [])
        if surface not in aliases and len(aliases) < MAX_ALIASES:
            measure.aliases = sorted({*aliases, surface})
        self._by_alias[alias_key(surface)] = measure
        # A measure with no unit class yet adopts the first one it is seen with, which is
        # what lets later candidates be blocked from merging across classes.
        self._session.add(measure)


class EntityRegistry:
    """Resolves subject surfaces, batched for the same reason as the measure registry.

    This one mattered more: `resolve` was called once per fact rather than once per distinct
    subject, so a document with eight hundred facts about one company ran eight hundred
    embedding inferences to answer the same question every time.
    """

    def __init__(self, session: Session, *, document_id: int | None = None) -> None:
        self._session = session
        self._document_id = document_id
        self._entities: list[Entity] = list(session.scalars(select(Entity)))
        self._by_alias: dict[str, Entity] = {}
        for entity in self._entities:
            for alias in [entity.name, *(entity.aliases or [])]:
                self._by_alias.setdefault(alias_key(alias), entity)
                self._by_alias.setdefault(entity_alias_key(alias), entity)

        dimensions = embed.dimensions()
        self._vectors: list[np.ndarray] = [
            embed.from_blob(entity.embedding, dimensions)
            if entity.embedding
            else np.zeros(dimensions, dtype=np.float32)
            for entity in self._entities
        ]
        self.report = RegistryReport()

    def resolve_many(self, surfaces: list[str]) -> dict[str, Entity]:
        """Resolve a batch of subject surfaces, embedding only the unrecognised ones."""
        resolved: dict[str, Entity] = {}
        pending: list[str] = []

        for raw in surfaces:
            surface = raw.strip()
            if not surface or surface in resolved:
                continue
            existing = self._lookup(surface)
            if existing is not None:
                self._add_alias(existing, surface)
                self.report.entities_linked += 1
                resolved[surface] = existing
            elif surface not in pending:
                pending.append(surface)

        if not pending:
            return resolved

        queries = embed.embed_texts(pending)
        for surface, query in zip(pending, queries, strict=False):
            existing = self._lookup(surface)
            if existing is not None:
                self._add_alias(existing, surface)
                self.report.entities_linked += 1
                resolved[surface] = existing
                continue

            if self._vectors:
                matrix = np.vstack(self._vectors)
                matches = embed.top_matches(
                    query, matrix, limit=1, threshold=ENTITY_CREATE_THRESHOLD
                )
                if matches:
                    index, score = matches[0]
                    other = self._entities[index]
                    if score >= ENTITY_LINK_THRESHOLD and _same_organisation(surface, other.name):
                        self._add_alias(other, surface)
                        self.report.entities_linked += 1
                        resolved[surface] = other
                        continue

            resolved[surface] = self._create(surface, None, vector=query)
        return resolved

    def resolve(self, surface: str, entity_type: str | None = None) -> Entity | None:
        return self.resolve_many([surface]).get(surface.strip())

    def _lookup(self, surface: str) -> Entity | None:
        for key in (alias_key(surface), entity_alias_key(surface)):
            existing = self._by_alias.get(key)
            if existing is not None:
                return existing
        return None

    def _create(
        self, surface: str, entity_type: str | None, *, vector: np.ndarray | None = None
    ) -> Entity:
        if vector is None:
            vector = embed.embed_text(surface)
        entity = Entity(
            slug=_unique_slug(self._session, Entity, slugify(surface)),
            name=surface,
            entity_type=entity_type,
            aliases=[surface],
            embedding=embed.to_blob(vector),
            first_seen_document_id=self._document_id,
        )
        self._session.add(entity)
        self._session.flush()
        self._entities.append(entity)
        self._vectors.append(vector)
        self._by_alias[alias_key(surface)] = entity
        self._by_alias[entity_alias_key(surface)] = entity
        self.report.entities_created += 1
        return entity

    def _add_alias(self, entity: Entity, surface: str) -> None:
        aliases = list(entity.aliases or [])
        if surface not in aliases and len(aliases) < MAX_ALIASES:
            entity.aliases = sorted({*aliases, surface})
            self._session.add(entity)
        self._by_alias[alias_key(surface)] = entity
        self._by_alias[entity_alias_key(surface)] = entity


def record_qualifier_keys(
    session: Session, qualifiers: dict[str, str], document_id: int | None
) -> None:
    """Track the qualifier dimensions the corpus has introduced.

    These are the axes along which two facts can differ without contradicting each other,
    so knowing which ones exist is what lets the interface show the schema widening as new
    kinds of document arrive.
    """
    for key, value in qualifiers.items():
        row = session.scalar(select(QualifierKey).where(QualifierKey.key == key))
        if row is None:
            row = QualifierKey(key=key, values=[], first_seen_document_id=document_id)
            session.add(row)
            session.flush()
        values = list(row.values or [])
        if value and value not in values and len(values) < 200:
            row.values = sorted({*values, value})
        row.fact_count = (row.fact_count or 0) + 1
        session.add(row)


def _classes_compatible(left: str | None, right: str | None) -> bool:
    """Whether two measures could be the same thing given their unit classes.

    An unknown class is compatible with anything, because the first fact carrying a measure
    often has no resolvable unit. Two known and different classes never are.
    """
    if not left or not right:
        return True
    return left == right


def _unique_slug(session: Session, model: type, base: str) -> str:
    slug = base
    suffix = 2
    while session.scalar(select(model).where(model.slug == slug)) is not None:
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug
