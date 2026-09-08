import { useState } from "react";
import { Link } from "react-router-dom";

import EvidenceViewer from "./EvidenceViewer";
import { Chip, Quote } from "./primitives";
import {
  dimensionLabel,
  factLocation,
  formatBaseValue,
  formatPercent,
  formatPeriod,
  formatPeriodSpan,
  formatValue,
  relationLabel,
  titleCase,
} from "../lib/format";
import type { Fact, Relation } from "../lib/types";

/**
 * One relationship, shown as the two facts side by side.
 *
 * A verdict is not reviewable without the two things it is a verdict about, so both sides
 * are always rendered in full with their evidence rather than summarised. The differences
 * between them are computed here and marked, which is what makes the explanation checkable
 * instead of something to be taken on trust.
 */
export default function RelationCard({
  relation,
  defaultOpen = false,
}: {
  relation: Relation;
  defaultOpen?: boolean;
}) {
  const [showEvidence, setShowEvidence] = useState(defaultOpen);
  const differences = compare(relation.left, relation.right);

  return (
    <article className="relation">
      <header className="relation__head">
        <div className="relation__labels">
          <Chip tone={relation.type}>{relationLabel(relation.type)}</Chip>
          {relation.dimension !== "none" ? (
            <Chip title="The contextual dimension that accounts for the difference">
              {dimensionLabel(relation.dimension)}
            </Chip>
          ) : null}
          {relation.cross_document ? <Chip>Across documents</Chip> : <Chip>Same document</Chip>}
          <Chip
            title={
              relation.decided_by === "rule"
                ? "Decided mechanically, with no model call"
                : "The rules could not decide, so the evidence was read in context"
            }
          >
            {relation.decided_by === "rule" ? `Rule: ${relation.rule_id}` : "Reviewed in context"}
          </Chip>
          {relation.order_sensitive ? (
            <Chip tone="contradicts" title={unsettledExplanation(relation)}>
              Unsettled on re-reading
            </Chip>
          ) : null}
        </div>
        <div className="relation__metrics meta">
          {relation.delta_relative !== null && relation.delta_relative > 0 ? (
            <span>Δ {formatPercent(relation.delta_relative)}</span>
          ) : null}
          {relation.type === "contradicts" ? (
            <span>severity {relation.severity.toFixed(2)}</span>
          ) : null}
          <span>confidence {relation.confidence.toFixed(2)}</span>
        </div>
      </header>

      <p className="relation__explanation">{relation.explanation}</p>

      {relation.selected_because?.length ? (
        <ul className="relation__reasons">
          {relation.selected_because.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      ) : null}

      <div className="relation__pair">
        <FactSide
          fact={relation.left}
          differences={differences}
          side="left"
          superseded={relation.superseded_fact_id === relation.left.id}
        />
        <FactSide
          fact={relation.right}
          differences={differences}
          side="right"
          superseded={relation.superseded_fact_id === relation.right.id}
        />
      </div>

      <div className="relation__footer">
        <button
          type="button"
          className="btn btn--sm"
          onClick={() => setShowEvidence((value) => !value)}
        >
          {showEvidence ? "Hide source pages" : "Show source pages"}
        </button>
      </div>

      {showEvidence ? (
        <div className="relation__evidence">
          <EvidenceViewer fact={relation.left} />
          <EvidenceViewer fact={relation.right} />
        </div>
      ) : null}
    </article>
  );
}

/** Why a verdict is marked unsettled, in the words a reader needs to judge it. */
function unsettledExplanation(relation: Relation): string {
  const other = relation.reverse_relation_type
    ? `read it as ${relationLabel(relation.reverse_relation_type).toLowerCase()}`
    : "disagreed";
  return (
    `Read a second time with the two facts swapped, the same comparison ${other}. ` +
    "The verdict depends on which fact came first, so it is recorded as unsettled."
  );
}

interface Differences {
  period: boolean;
  unit: boolean;
  currency: boolean;
  basis: boolean;
  qualifiers: boolean;
  document: boolean;
  measureWording: boolean;
}

function compare(left: Fact, right: Fact): Differences {
  return {
    period: formatPeriod(left) !== formatPeriod(right),
    unit: (left.unit ?? "") !== (right.unit ?? ""),
    currency: (left.currency ?? "") !== (right.currency ?? ""),
    basis: (left.basis ?? "") !== (right.basis ?? ""),
    qualifiers: JSON.stringify(left.qualifiers) !== JSON.stringify(right.qualifiers),
    document: left.source.document_id !== right.source.document_id,
    measureWording: left.predicate !== right.predicate,
  };
}

function FactSide({
  fact,
  differences,
  side,
  superseded = false,
}: {
  fact: Fact;
  differences: Differences;
  side: "left" | "right";
  superseded?: boolean;
}) {
  const span = formatPeriodSpan(fact);
  return (
    <div className="relation__side" data-side={side} data-superseded={superseded || undefined}>
      <div className="relation__source">
        <span className="relation__document">{fact.source.document_title}</span>
        <span className="meta">{factLocation(fact)}</span>
      </div>

      {superseded ? (
        <p className="relation__superseded">Superseded by the other figure</p>
      ) : null}

      <p className="relation__statement">{fact.statement}</p>

      <dl className="relation__fields">
        <Row label="Value" changed={false}>
          <strong>{formatValue(fact)}</strong>
        </Row>
        <Row label="Normalised" changed={differences.unit || differences.currency}>
          {formatBaseValue(fact)}
          {fact.unit ? <span className="meta"> · {fact.unit}</span> : null}
        </Row>
        <Row label="Period" changed={differences.period}>
          {formatPeriod(fact)}
          {span ? <span className="meta"> · {span}</span> : null}
        </Row>
        <Row label="Measure" changed={differences.measureWording}>
          {fact.predicate}
        </Row>
        {fact.basis || differences.basis ? (
          <Row label="Basis" changed={differences.basis}>
            {fact.basis ? titleCase(fact.basis) : "not stated"}
          </Row>
        ) : null}
        {Object.keys(fact.qualifiers).length || differences.qualifiers ? (
          <Row label="Scope" changed={differences.qualifiers}>
            {Object.keys(fact.qualifiers).length
              ? Object.entries(fact.qualifiers).map(([key, value]) => (
                  <Chip key={key}>
                    {key}: {value}
                  </Chip>
                ))
              : "not scoped"}
          </Row>
        ) : null}
      </dl>

      <Quote text={fact.evidence.quote} highlight={fact.value_text} />

      <Link className="relation__link" to={`/facts?q=${encodeURIComponent(fact.statement)}`}>
        Open fact
      </Link>
    </div>
  );
}

function Row({
  label,
  changed,
  children,
}: {
  label: string;
  changed: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={changed ? "relation__row relation__row--changed" : "relation__row"}>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}
