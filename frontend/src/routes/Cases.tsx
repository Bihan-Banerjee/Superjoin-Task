import { useQuery } from "@tanstack/react-query";

import RelationCard from "../components/RelationCard";
import { Empty, ErrorNote, Loading, Panel } from "../components/primitives";
import { api } from "../lib/api";
import { formatPercent, titleCase } from "../lib/format";
import type { CaseBlock, Rejection, Relation } from "../lib/types";

/**
 * The four cases the assignment asks to be demonstrated.
 *
 * Everything on this page is queried from the knowledge layer at request time. Ingest a
 * different corpus and the page answers from that corpus, which is the only version of
 * this feature worth building — a fixed list of examples would prove the documents
 * contained them, not that the system found them.
 */
export default function Cases() {
  const { data, isLoading, error } = useQuery({ queryKey: ["cases"], queryFn: api.getCases });

  if (isLoading) return <Loading label="Selecting examples" />;
  if (error) return <ErrorNote error={error} />;

  const cases = data?.cases ?? [];

  return (
    <div className="page page--wide">
      <p className="page__intro">
        The four cases the assignment asks for, chosen from whatever is currently in the
        knowledge layer. Each example says why it was selected, so the choice can be argued
        with rather than accepted.
      </p>

      {cases.map((block, index) => (
        <section key={block.key} className="case">
          <header className="case__head">
            <span className="case__number">{index + 1}</span>
            <div>
              <h2>{block.title}</h2>
              <p className="case__description">{block.description}</p>
            </div>
          </header>

          {block.key === "failure" ? (
            <FailureCase block={block} />
          ) : block.found ? (
            <div className="case__examples">
              {(block.examples as Relation[]).map((relation) => (
                <RelationCard key={relation.id} relation={relation} defaultOpen={false} />
              ))}
            </div>
          ) : (
            <Empty>
              Nothing of this kind is in the knowledge layer yet. Ingest at least two
              documents that overlap in subject matter.
            </Empty>
          )}
        </section>
      ))}
    </div>
  );
}

function FailureCase({ block }: { block: CaseBlock }) {
  const summary = block.summary;
  const reasons = block.by_reason ?? [];
  const examples = (block.examples as Rejection[]) ?? [];

  if (!block.found) {
    return <Empty>Nothing has been rejected yet, which usually means nothing has been ingested.</Empty>;
  }

  return (
    <div className="case__examples">
      {summary ? (
        <Panel title="What the pipeline discarded">
          <p>
            Of {(summary.facts_kept + summary.candidates_rejected).toLocaleString()} candidate
            facts proposed across the corpus, {summary.facts_kept.toLocaleString()} were kept
            and {summary.candidates_rejected.toLocaleString()} were refused — a grounding
            pass rate of {formatPercent(summary.grounding_pass_rate)}.
          </p>
          <p>
            A refusal is not a bug being hidden. The pipeline could not verify the claim
            against its source, so it declined to assert it. The breakdown below is the
            honest account of where reading these documents goes wrong.
          </p>

          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Reason</th>
                  <th className="numeric">Count</th>
                  <th>What it means</th>
                </tr>
              </thead>
              <tbody>
                {reasons.map((row) => (
                  <tr key={row.reason}>
                    <td>{titleCase(row.reason)}</td>
                    <td className="numeric">{row.count.toLocaleString()}</td>
                    <td>{row.explanation}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      ) : null}

      {examples.length ? (
        <Panel title="Examples">
          <div className="rejection-list">
            {examples.map((rejection) => (
              <div key={rejection.id} className="rejection">
                <div className="rejection__head">
                  <span className="chip">{titleCase(rejection.reason)}</span>
                  <span className="meta">
                    {rejection.document_title}
                    {rejection.page_number ? ` · page ${rejection.page_number}` : ""}
                  </span>
                </div>
                <p className="rejection__detail">{rejection.detail}</p>
                {Object.keys(rejection.candidate).length ? (
                  <pre className="rejection__candidate">
                    {JSON.stringify(rejection.candidate, null, 2)}
                  </pre>
                ) : null}
              </div>
            ))}
          </div>
        </Panel>
      ) : null}
    </div>
  );
}
