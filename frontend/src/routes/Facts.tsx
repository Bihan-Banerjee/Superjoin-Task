import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import EvidenceViewer from "../components/EvidenceViewer";
import {
  Chip,
  Empty,
  ErrorNote,
  Field,
  Loading,
  MetaList,
  Pagination,
  Panel,
  Quote,
} from "../components/primitives";
import { api } from "../lib/api";
import {
  dimensionLabel,
  factLocation,
  formatBaseValue,
  formatNumber,
  formatPeriod,
  formatPeriodSpan,
  formatValue,
  relationLabel,
  titleCase,
  truncate,
} from "../lib/format";
import type { Fact, FactDetail, Relation } from "../lib/types";

const LIMIT = 60;

export default function Facts() {
  const [params, setParams] = useSearchParams();
  const [selected, setSelected] = useState<number | null>(null);
  const [search, setSearch] = useState(params.get("q") ?? "");

  const documentId = params.get("document_id");
  const measureId = params.get("measure_id");
  const entityId = params.get("entity_id");
  const unitClass = params.get("unit_class") ?? "";
  const kind = params.get("kind") ?? "";
  const basis = params.get("basis") ?? "";
  const minConfidence = params.get("min_confidence") ?? "";
  const hasRelations = params.get("has_relations") ?? "";
  const offset = Number(params.get("offset") ?? 0);
  const query = params.get("q") ?? "";

  // Debounced so typing does not fire a request per keystroke.
  useEffect(() => {
    const handle = window.setTimeout(() => {
      if (search === query) return;
      update({ q: search || null, offset: null });
    }, 300);
    return () => window.clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  const update = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value === null || value === "") next.delete(key);
      else next.set(key, value);
    }
    setParams(next, { replace: true });
  };

  const { data: documents } = useQuery({ queryKey: ["documents"], queryFn: api.listDocuments });
  const { data: measures } = useQuery({
    queryKey: ["measures", "all"],
    queryFn: () => api.listMeasures({ sort: "facts", limit: 500 }),
  });

  const filters = {
    document_id: documentId,
    measure_id: measureId,
    entity_id: entityId,
    unit_class: unitClass,
    kind,
    basis,
    min_confidence: minConfidence,
    has_relations: hasRelations,
    q: query,
    limit: LIMIT,
    offset,
  };

  const { data, isLoading, error } = useQuery({
    queryKey: ["facts", filters],
    queryFn: () => api.listFacts(filters),
  });

  const facts = data?.facts ?? [];
  const active = facts.find((fact) => fact.id === selected) ?? null;

  const unitClasses = Array.from(
    new Set((measures?.unit_classes ?? []).map((item) => item.unit_class).filter(Boolean)),
  ) as string[];

  return (
    <div className="split">
      <div className="split__main">
        <Panel flush>
          <div className="toolbar">
            <Field label="Search">
              <input
                className="input"
                type="search"
                placeholder="statement, subject or quote"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                style={{ width: 220 }}
              />
            </Field>
            <Field label="Document">
              <select
                className="select"
                value={documentId ?? ""}
                onChange={(event) => update({ document_id: event.target.value, offset: null })}
              >
                <option value="">All</option>
                {documents?.documents.map((document) => (
                  <option key={document.id} value={document.id}>
                    {truncate(document.title, 40)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Measure">
              <select
                className="select"
                value={measureId ?? ""}
                onChange={(event) => update({ measure_id: event.target.value, offset: null })}
              >
                <option value="">All</option>
                {measures?.measures.map((measure) => (
                  <option key={measure.id} value={measure.id}>
                    {truncate(measure.name, 40)} ({measure.fact_count})
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Unit class">
              <select
                className="select"
                value={unitClass}
                onChange={(event) => update({ unit_class: event.target.value, offset: null })}
              >
                <option value="">All</option>
                {unitClasses.map((value) => (
                  <option key={value} value={value}>
                    {titleCase(value)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Basis">
              <select
                className="select"
                value={basis}
                onChange={(event) => update({ basis: event.target.value, offset: null })}
              >
                <option value="">All</option>
                {["actual", "estimate", "projection", "revised", "restated", "pro_forma"].map(
                  (value) => (
                    <option key={value} value={value}>
                      {titleCase(value)}
                    </option>
                  ),
                )}
              </select>
            </Field>
            <Field label="Linked">
              <select
                className="select"
                value={hasRelations}
                onChange={(event) => update({ has_relations: event.target.value, offset: null })}
              >
                <option value="">All</option>
                <option value="true">Has relations</option>
                <option value="false">No relations</option>
              </select>
            </Field>
            <div className="toolbar__spacer" />
            <button
              type="button"
              className="btn btn--sm btn--quiet"
              onClick={() => {
                setSearch("");
                setParams(new URLSearchParams(), { replace: true });
              }}
            >
              Clear
            </button>
          </div>

          {isLoading ? <Loading label="Loading facts" /> : null}
          {error ? <ErrorNote error={error} /> : null}
          {!isLoading && !facts.length ? <Empty>No facts match these filters.</Empty> : null}

          {facts.length ? (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Statement</th>
                    <th>Subject</th>
                    <th>Measure</th>
                    <th className="numeric">Value</th>
                    <th className="numeric">Normalised</th>
                    <th>Period</th>
                    <th>Source</th>
                    <th className="numeric">Conf.</th>
                  </tr>
                </thead>
                <tbody>
                  {facts.map((fact) => (
                    <tr
                      key={fact.id}
                      className={
                        fact.id === selected ? "clickable is-selected" : "clickable"
                      }
                      onClick={() => setSelected(fact.id)}
                    >
                      <td>
                        <span className="truncate" title={fact.statement}>
                          {fact.statement}
                        </span>
                        {Object.keys(fact.qualifiers).length ? (
                          <div className="chip-row">
                            {Object.entries(fact.qualifiers).map(([key, value]) => (
                              <Chip key={key}>
                                {key}: {value}
                              </Chip>
                            ))}
                          </div>
                        ) : null}
                      </td>
                      <td>{truncate(fact.subject, 28)}</td>
                      <td title={fact.predicate}>{truncate(fact.measure ?? fact.predicate, 30)}</td>
                      <td className="numeric">{formatValue(fact)}</td>
                      <td className="numeric meta">{formatBaseValue(fact)}</td>
                      <td>
                        {formatPeriod(fact)}
                        {fact.basis ? (
                          <div className="meta">{titleCase(fact.basis)}</div>
                        ) : null}
                      </td>
                      <td className="meta">{factLocation(fact)}</td>
                      <td className="numeric">{fact.confidence.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          {data ? (
            <div className="panel__footer">
              <Pagination
                total={data.total}
                limit={data.limit}
                offset={data.offset}
                onChange={(next) => update({ offset: String(next) })}
              />
            </div>
          ) : null}
        </Panel>
      </div>

      <aside className="split__aside">
        {active ? (
          <FactPanel fact={active} onClose={() => setSelected(null)} />
        ) : (
          <div className="empty">Select a fact to see its evidence.</div>
        )}
      </aside>
    </div>
  );
}

function FactPanel({ fact, onClose }: { fact: Fact; onClose: () => void }) {
  const { data } = useQuery({ queryKey: ["fact", fact.id], queryFn: () => api.getFact(fact.id) });
  // The row from the list is shown immediately; relations arrive with the detail request.
  const detail: Fact | FactDetail = data ?? fact;
  const relations: Relation[] = data?.relations ?? [];
  const span = formatPeriodSpan(detail);

  return (
    <div className="detail">
      <div className="detail__head">
        <h2>{detail.statement}</h2>
        <button type="button" className="btn btn--sm btn--quiet" onClick={onClose}>
          Close
        </button>
      </div>

      <MetaList
        items={[
          ["Subject", detail.entity ?? detail.subject],
          ["Measure", detail.measure ?? detail.predicate],
          ["Value", formatValue(detail)],
          ["Normalised", `${formatBaseValue(detail)} · ${detail.unit ?? "no unit"}`],
          ["Period", span ? `${formatPeriod(detail)} (${span})` : formatPeriod(detail)],
          ["Basis", detail.basis ? titleCase(detail.basis) : null],
          [
            "Qualifiers",
            Object.keys(detail.qualifiers).length
              ? Object.entries(detail.qualifiers)
                  .map(([key, value]) => `${key}: ${value}`)
                  .join(", ")
              : null,
          ],
          ["Confidence", detail.confidence.toFixed(2)],
          [
            "Evidence match",
            detail.evidence.exact
              ? "exact"
              : `approximate (${formatNumber(detail.evidence.score, 0)}%)`,
          ],
          ["Source", `${detail.source.document_title} · ${factLocation(detail)}`],
        ]}
      />

      <EvidenceViewer fact={detail} />

      {relations.length ? (
        <div className="detail__section">
          <h3>Related facts ({relations.length})</h3>
          {relations.map((relation) => {
            const other = relation.left.id === detail.id ? relation.right : relation.left;
            return (
              <div key={relation.id} className="related">
                <div className="related__head">
                  <Chip tone={relation.type}>{relationLabel(relation.type)}</Chip>
                  {relation.dimension !== "none" ? (
                    <Chip>{dimensionLabel(relation.dimension)}</Chip>
                  ) : null}
                  <span className="meta">
                    {relation.decided_by === "rule" ? relation.rule_id : "reviewed in context"}
                  </span>
                </div>
                <p className="related__explanation">{relation.explanation}</p>
                <div className="related__fact">
                  <span>{other.statement}</span>
                  <span className="meta">
                    {formatValue(other)} · {other.source.document_title} · {factLocation(other)}
                  </span>
                </div>
                <Quote text={other.evidence.quote} highlight={other.value_text} />
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
