import { useQuery } from "@tanstack/react-query";

import { ErrorNote, Loading, Panel } from "../components/primitives";
import { api } from "../lib/api";
import { formatNumber, formatPercent, titleCase } from "../lib/format";

/**
 * Everything here is computed from what the pipeline recorded while running. None of it is
 * a claim the code makes about itself, which is the point: the honest answer to "how well
 * does this work" is a number the system produced, not a paragraph in a README.
 */
export default function Evaluation() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["evaluation"],
    queryFn: api.getEvaluation,
  });

  if (isLoading) return <Loading label="Computing metrics" />;
  if (error) return <ErrorNote error={error} />;
  if (!data) return null;

  const { grounding, normalisation, relations, registry, model_usage: usage, corpus } = data;

  return (
    <div className="page page--wide">
      <p className="page__intro">
        Measured from the last run over whatever is currently ingested. Grounding pass rate
        is the share of proposed facts whose evidence could be verified against the source
        page; everything below it is a ceiling on what the linking stage could find.
      </p>

      <div className="metrics">
        <Metric
          label="Grounding pass rate"
          value={formatPercent(grounding.pass_rate)}
          note={`${formatNumber(grounding.facts_kept, 0)} kept of ${formatNumber(grounding.candidates_proposed, 0)} proposed`}
        />
        <Metric
          label="Quotes matched exactly"
          value={formatPercent(grounding.exact_match_rate)}
          note="the rest matched after normalising ligatures and line wrapping"
        />
        <Metric
          label="Located on the page"
          value={formatPercent(grounding.page_location_rate)}
          note="resolved to rectangles for highlighting"
        />
        <Metric
          label="Model cache hits"
          value={formatPercent(usage.cache_hit_rate)}
          note={`${formatNumber(usage.billed_calls, 0)} of ${formatNumber(usage.calls, 0)} calls actually billed`}
        />
      </div>

      <div className="grid-two">
        <Panel title="Normalisation">
          <p className="meta">
            A fact that did not reach a comparable form can still be read, but it cannot
            participate in most comparisons.
          </p>
          <Rows
            rows={[
              ["Facts", formatNumber(normalisation.facts, 0)],
              ["Numeric", formatNumber(normalisation.numeric, 0)],
              [
                "Unit resolved",
                `${formatNumber(normalisation.unit_resolved, 0)} (${formatPercent(normalisation.unit_resolution_rate)} of numeric)`,
              ],
              [
                "Period resolved",
                `${formatNumber(normalisation.period_resolved, 0)} (${formatPercent(normalisation.period_resolution_rate)})`,
              ],
              [
                "Measure assigned",
                `${formatNumber(normalisation.measure_assigned, 0)} (${formatPercent(normalisation.measure_assignment_rate)})`,
              ],
            ]}
          />
          <Bars
            title="Facts by unit class"
            data={normalisation.by_unit_class.map((row) => ({
              label: titleCase(row.unit_class),
              count: row.count,
            }))}
          />
        </Panel>

        <Panel title="Rejections by reason">
          <p className="meta">
            Every candidate fact the pipeline refused, and why. These are recorded rather
            than logged so the failure rate is measurable.
          </p>
          <Bars
            data={data.rejections_by_reason.map((row) => ({
              label: titleCase(row.reason),
              count: row.count,
            }))}
          />
        </Panel>
      </div>

      <div className="grid-two">
        <Panel title="Relations">
          <Rows
            rows={[
              ["Total", formatNumber(relations.total, 0)],
              [
                "Across documents",
                `${formatNumber(relations.cross_document, 0)} (${formatPercent(
                  relations.total ? relations.cross_document / relations.total : 0,
                )})`,
              ],
              ...relations.by_decision.map(
                (row) =>
                  [
                    row.decided_by === "rule" ? "Decided by rule" : "Read in context",
                    formatNumber(row.count, 0),
                  ] as [string, string],
              ),
            ]}
          />
          <Bars
            title="By type"
            data={relations.by_type.map((row) => ({
              label: titleCase(row.type),
              count: row.count,
            }))}
          />
          <Bars
            title="By explaining dimension"
            data={relations.by_dimension
              .filter((row) => row.dimension !== "none")
              .map((row) => ({ label: titleCase(row.dimension), count: row.count }))}
          />
        </Panel>

        <Panel title="Rules that fired">
          <p className="meta">
            How often each deterministic rule decided a pair without a model call.
          </p>
          <Bars
            data={relations.by_rule.map((row) => ({ label: row.rule_id, count: row.count }))}
          />

          <h3 className="panel__subhead">Model agreement with itself</h3>
          <p className="meta">
            Pairs the rules could not settle are read twice, with the two facts swapped. A
            verdict that changes on the second reading was about the ordering rather than the
            evidence, so it is kept but marked unsettled rather than trusted.
          </p>
          <Rows
            rows={[
              ["Pairs read in context", formatNumber(relations.adjudication.decided_by_model, 0)],
              [
                "Changed on re-reading",
                `${formatNumber(relations.adjudication.order_sensitive, 0)} (${formatPercent(
                  relations.adjudication.order_sensitive_rate,
                )})`,
              ],
            ]}
          />
        </Panel>
      </div>

      <div className="grid-two">
        <Panel title="Corpus">
          <Rows
            rows={[
              ["Documents", formatNumber(corpus.documents, 0)],
              ["Pages", formatNumber(corpus.pages, 0)],
              [
                "Pages sent to a model",
                `${formatNumber(corpus.pages_extracted, 0)} (${formatPercent(
                  corpus.pages ? corpus.pages_extracted / corpus.pages : 0,
                )})`,
              ],
              ["Measures", formatNumber(registry.measures, 0)],
              [
                "Measures in more than one document",
                `${formatNumber(registry.measures_in_multiple_documents, 0)} (${formatPercent(registry.shared_measure_rate)})`,
              ],
              ["Entities", formatNumber(registry.entities, 0)],
              ["Qualifier dimensions", formatNumber(registry.qualifier_keys, 0)],
            ]}
          />
          <Bars
            title="Pages by type"
            data={corpus.page_types.map((row) => ({
              label: titleCase(row.page_type),
              count: row.count,
            }))}
          />
        </Panel>

        <Panel title="Model usage">
          <Rows
            rows={[
              ["Calls", formatNumber(usage.calls, 0)],
              ["Billed", formatNumber(usage.billed_calls, 0)],
              ["Cache hits", formatNumber(usage.cache_hits, 0)],
              ["Failed", formatNumber(usage.failed_calls, 0)],
              ["Input tokens", formatNumber(usage.input_tokens, 0)],
              ["Output tokens", formatNumber(usage.output_tokens, 0)],
              ["Median latency", `${formatNumber(usage.median_latency_ms, 0)} ms`],
            ]}
          />
          <Bars
            title="By purpose"
            data={usage.by_purpose.map((row) => ({
              label: titleCase(row.purpose),
              count: row.count,
            }))}
          />
        </Panel>
      </div>

      <Panel title="Per-document coverage" flush>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Document</th>
                <th className="numeric">Pages</th>
                <th className="numeric">Pages read</th>
                <th className="numeric">Facts</th>
                <th className="numeric">Facts per page read</th>
                <th className="numeric">Rejected</th>
                <th className="numeric">Kept</th>
              </tr>
            </thead>
            <tbody>
              {data.coverage.map((row) => {
                const proposed = row.facts + row.rejections;
                return (
                  <tr key={row.document_id}>
                    <td>{row.title}</td>
                    <td className="numeric">{formatNumber(row.page_count, 0)}</td>
                    <td className="numeric">{formatNumber(row.pages_extracted, 0)}</td>
                    <td className="numeric">{formatNumber(row.facts, 0)}</td>
                    <td className="numeric">{row.facts_per_extracted_page.toFixed(2)}</td>
                    <td className="numeric">{formatNumber(row.rejections, 0)}</td>
                    <td className="numeric">
                      {proposed ? formatPercent(row.facts / proposed, 0) : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}

function Metric({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="metric">
      <span className="metric__label">{label}</span>
      <span className="metric__value">{value}</span>
      <span className="metric__note">{note}</span>
    </div>
  );
}

function Rows({ rows }: { rows: [string, string][] }) {
  return (
    <table className="data data--compact">
      <tbody>
        {rows.map(([label, value]) => (
          <tr key={label}>
            <td>{label}</td>
            <td className="numeric">{value}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/**
 * A count breakdown as proportional bars.
 *
 * Deliberately not a chart library: these are always one-dimensional counts, and a bar
 * whose width is a percentage of the largest value communicates the same thing without a
 * dependency or a canvas.
 */
function Bars({ title, data }: { title?: string; data: { label: string; count: number }[] }) {
  if (!data.length) return null;
  const max = Math.max(...data.map((row) => row.count));

  return (
    <div className="bars">
      {title ? <h3 className="bars__title">{title}</h3> : null}
      {data.map((row) => (
        <div key={row.label} className="bars__row">
          <span className="bars__label" title={row.label}>
            {row.label}
          </span>
          <span className="bars__track">
            <span className="bars__fill" style={{ width: `${(row.count / max) * 100}%` }} />
          </span>
          <span className="bars__count">{formatNumber(row.count, 0)}</span>
        </div>
      ))}
    </div>
  );
}
