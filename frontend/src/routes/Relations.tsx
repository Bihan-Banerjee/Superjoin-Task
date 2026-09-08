import { useQuery } from "@tanstack/react-query";
import { Suspense, lazy, useState } from "react";
import { useSearchParams } from "react-router-dom";

import RelationCard from "../components/RelationCard";
import {
  Empty,
  ErrorNote,
  ExportLinks,
  Field,
  Loading,
  Pagination,
  Panel,
} from "../components/primitives";
import { api } from "../lib/api";
import { dimensionLabel, relationLabel, truncate } from "../lib/format";

// Loaded only when someone opens the graph, so the layout library is a chunk the browser
// never fetches on a deployment that leaves the view switched off.
const RelationGraph = lazy(() => import("../components/RelationGraph"));

const LIMIT = 20;

const TYPES = [
  "corroborates",
  "contradicts",
  "reconciled_by_context",
  "refines",
  "supersedes",
];
const DIMENSIONS = [
  "period",
  "unit_scale",
  "currency",
  "scope",
  "segment",
  "basis",
  "vintage",
  "definition",
];

export default function Relations() {
  const [params, setParams] = useSearchParams();

  const relationType = params.get("relation_type") ?? "";
  const dimension = params.get("dimension") ?? "";
  const decidedBy = params.get("decided_by") ?? "";
  const documentId = params.get("document_id") ?? "";
  const crossDocument = params.get("cross_document") ?? "";
  const sort = params.get("sort") ?? "severity";
  const offset = Number(params.get("offset") ?? 0);

  const update = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value === null || value === "") next.delete(key);
      else next.set(key, value);
    }
    if (!("offset" in changes)) next.delete("offset");
    setParams(next, { replace: true });
  };

  const { data: documents } = useQuery({ queryKey: ["documents"], queryFn: api.listDocuments });
  const { data: health } = useQuery({ queryKey: ["health"], queryFn: api.health });
  const graphAvailable = Boolean(health?.graph_view_enabled);
  const [asGraph, setAsGraph] = useState(false);
  const [selectedRelationId, setSelectedRelationId] = useState<number | null>(null);
  const showGraph = graphAvailable && asGraph;

  const filters = {
    relation_type: relationType,
    dimension,
    decided_by: decidedBy,
    document_id: documentId,
    cross_document: crossDocument,
    sort,
    limit: LIMIT,
    offset,
  };

  const { data, isLoading, error } = useQuery({
    queryKey: ["relations", filters],
    queryFn: () => api.listRelations(filters),
  });

  const graphFilters = {
    relation_type: relationType,
    dimension,
    decided_by: decidedBy,
    document_id: documentId,
    cross_document: crossDocument,
  };

  const { data: graph, error: graphError } = useQuery({
    queryKey: ["relation-graph", graphFilters],
    queryFn: () => api.relationGraph(graphFilters),
    enabled: showGraph,
  });

  const { data: selectedRelation } = useQuery({
    queryKey: ["relation", selectedRelationId],
    queryFn: () => api.getRelation(selectedRelationId as number),
    enabled: showGraph && selectedRelationId !== null,
  });

  const relations = data?.relations ?? [];
  const counts = data?.counts ?? {};

  return (
    <div className="page page--wide">
      <p className="page__intro">
        Every pair of facts the system judged to be about the same thing, and what it
        concluded. Verdicts marked with a rule were decided mechanically; the rest reached a
        model only because the rules could not settle them.
      </p>

      <div className="filter-bar">
        <button
          type="button"
          className={relationType === "" ? "filter-tab is-active" : "filter-tab"}
          onClick={() => update({ relation_type: null })}
        >
          All <span className="meta">{data?.total ?? 0}</span>
        </button>
        {TYPES.map((type) => (
          <button
            key={type}
            type="button"
            className={relationType === type ? "filter-tab is-active" : "filter-tab"}
            onClick={() => update({ relation_type: type })}
          >
            {relationLabel(type)} <span className="meta">{counts[type] ?? 0}</span>
          </button>
        ))}
      </div>

      <Panel flush>
        <div className="toolbar">
          <Field label="Dimension">
            <select
              className="select"
              value={dimension}
              onChange={(event) => update({ dimension: event.target.value })}
            >
              <option value="">Any</option>
              {DIMENSIONS.map((value) => (
                <option key={value} value={value}>
                  {dimensionLabel(value)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Decided by">
            <select
              className="select"
              value={decidedBy}
              onChange={(event) => update({ decided_by: event.target.value })}
            >
              <option value="">Either</option>
              <option value="rule">Rule</option>
              <option value="model">Read in context</option>
            </select>
          </Field>
          <Field label="Scope">
            <select
              className="select"
              value={crossDocument}
              onChange={(event) => update({ cross_document: event.target.value })}
            >
              <option value="">All pairs</option>
              <option value="true">Across documents</option>
              <option value="false">Within one document</option>
            </select>
          </Field>
          <Field label="Document">
            <select
              className="select"
              value={documentId}
              onChange={(event) => update({ document_id: event.target.value })}
            >
              <option value="">All</option>
              {documents?.documents.map((document) => (
                <option key={document.id} value={document.id}>
                  {truncate(document.title, 40)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Sort">
            <select
              className="select"
              value={sort}
              onChange={(event) => update({ sort: event.target.value })}
            >
              <option value="severity">Severity</option>
              <option value="confidence">Confidence</option>
              <option value="id">Order found</option>
            </select>
          </Field>
          <div className="toolbar__spacer" />
          <ExportLinks resource="relations" params={{ document_id: documentId }} />
          {graphAvailable ? (
            <div className="view-switch">
              <button
                type="button"
                className={asGraph ? "btn btn--sm" : "btn btn--sm is-active"}
                onClick={() => setAsGraph(false)}
              >
                Table
              </button>
              <button
                type="button"
                className={asGraph ? "btn btn--sm is-active" : "btn btn--sm"}
                onClick={() => setAsGraph(true)}
              >
                Graph
              </button>
            </div>
          ) : null}
          <button
            type="button"
            className="btn btn--sm btn--quiet"
            onClick={() => setParams(new URLSearchParams(), { replace: true })}
          >
            Clear
          </button>
        </div>

        {showGraph ? (
          <div className="relation-list">
            {health?.graph_view_warning ? (
              <p className="graph__notice">{health.graph_view_warning}</p>
            ) : null}
            {graphError ? <ErrorNote error={graphError} /> : null}
            <Suspense fallback={<Loading label="Loading the graph" />}>
              {graph ? (
                <RelationGraph
                  data={graph}
                  selectedRelationId={selectedRelationId}
                  onSelectRelation={setSelectedRelationId}
                />
              ) : null}
            </Suspense>
            {selectedRelation ? <RelationCard relation={selectedRelation} /> : null}
          </div>
        ) : (
          <div className="relation-list">
            {isLoading ? <Loading label="Loading relations" /> : null}
            {error ? <ErrorNote error={error} /> : null}
            {!isLoading && !relations.length ? (
              <Empty>No relations match these filters.</Empty>
            ) : null}
            {relations.map((relation) => (
              <RelationCard key={relation.id} relation={relation} />
            ))}
          </div>
        )}

        {data && !showGraph ? (
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
  );
}
