import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { Chip, Empty, ErrorNote, Field, Loading, Panel } from "../components/primitives";
import { api } from "../lib/api";
import { formatNumber, titleCase, truncate } from "../lib/format";

type Tab = "measures" | "entities" | "qualifiers";

/**
 * The registry is the schema.
 *
 * Nothing in the code enumerates what can be measured. A document introducing a kind of
 * measurement the system has never seen adds a row here, and from that point facts using
 * either wording become comparable. This page exists so that growth is inspectable rather
 * than asserted in a README.
 */
export default function Registry() {
  const [tab, setTab] = useState<Tab>("measures");
  const [search, setSearch] = useState("");
  const [unitClass, setUnitClass] = useState("");
  const [sharedOnly, setSharedOnly] = useState(false);

  return (
    <div className="page page--wide">
      <p className="page__intro">
        Measures, entities and qualifier dimensions discovered from the documents. This is
        the schema: it is data, so a new kind of fact adds a row rather than requiring a
        migration. A measure appearing in more than one document is one the system can use
        to compare across sources.
      </p>

      <div className="filter-bar">
        {(["measures", "entities", "qualifiers"] as Tab[]).map((value) => (
          <button
            key={value}
            type="button"
            className={tab === value ? "filter-tab is-active" : "filter-tab"}
            onClick={() => setTab(value)}
          >
            {titleCase(value)}
          </button>
        ))}
      </div>

      {tab === "measures" ? (
        <Measures
          search={search}
          onSearch={setSearch}
          unitClass={unitClass}
          onUnitClass={setUnitClass}
          sharedOnly={sharedOnly}
          onSharedOnly={setSharedOnly}
        />
      ) : null}
      {tab === "entities" ? <Entities search={search} onSearch={setSearch} /> : null}
      {tab === "qualifiers" ? <Qualifiers /> : null}
    </div>
  );
}

function Measures({
  search,
  onSearch,
  unitClass,
  onUnitClass,
  sharedOnly,
  onSharedOnly,
}: {
  search: string;
  onSearch: (value: string) => void;
  unitClass: string;
  onUnitClass: (value: string) => void;
  sharedOnly: boolean;
  onSharedOnly: (value: boolean) => void;
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["measures", search, unitClass, sharedOnly],
    queryFn: () =>
      api.listMeasures({
        q: search,
        unit_class: unitClass,
        min_documents: sharedOnly ? 2 : 0,
        sort: "facts",
        limit: 500,
      }),
  });

  const measures = data?.measures ?? [];

  return (
    <Panel flush>
      <div className="toolbar">
        <Field label="Search">
          <input
            className="input"
            type="search"
            placeholder="measure name"
            value={search}
            onChange={(event) => onSearch(event.target.value)}
            style={{ width: 200 }}
          />
        </Field>
        <Field label="Unit class">
          <select
            className="select"
            value={unitClass}
            onChange={(event) => onUnitClass(event.target.value)}
          >
            <option value="">All</option>
            {(data?.unit_classes ?? [])
              .filter((row) => row.unit_class)
              .map((row) => (
                <option key={row.unit_class} value={row.unit_class ?? ""}>
                  {titleCase(row.unit_class ?? "")} ({row.count})
                </option>
              ))}
          </select>
        </Field>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={sharedOnly}
            onChange={(event) => onSharedOnly(event.target.checked)}
          />
          <span>Only measures seen in more than one document</span>
        </label>
        <div className="toolbar__spacer" />
        <span className="meta">
          {formatNumber(measures.length, 0)} of {formatNumber(data?.total ?? 0, 0)}
        </span>
      </div>

      {isLoading ? <Loading /> : null}
      {error ? <ErrorNote error={error} /> : null}
      {!isLoading && !measures.length ? <Empty>No measures registered yet.</Empty> : null}

      {measures.length ? (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Canonical measure</th>
                <th>Unit class</th>
                <th>Also written as</th>
                <th className="numeric">Facts</th>
                <th className="numeric">Documents</th>
                <th>First seen in</th>
              </tr>
            </thead>
            <tbody>
              {measures.map((measure) => (
                <tr key={measure.id}>
                  <td>
                    <Link to={`/facts?measure_id=${measure.id}`}>{measure.name}</Link>
                  </td>
                  <td>{measure.unit_class ? titleCase(measure.unit_class) : "—"}</td>
                  <td>
                    {measure.aliases.length > 1 ? (
                      <div className="chip-row">
                        {measure.aliases
                          .filter((alias) => alias !== measure.name)
                          .slice(0, 4)
                          .map((alias) => (
                            <Chip key={alias}>{truncate(alias, 34)}</Chip>
                          ))}
                        {measure.aliases.length > 5 ? (
                          <span className="meta">+{measure.aliases.length - 5} more</span>
                        ) : null}
                      </div>
                    ) : (
                      <span className="meta">—</span>
                    )}
                  </td>
                  <td className="numeric">{formatNumber(measure.fact_count, 0)}</td>
                  <td className="numeric">
                    {measure.document_count > 1 ? (
                      <strong>{measure.document_count}</strong>
                    ) : (
                      measure.document_count
                    )}
                  </td>
                  <td className="meta">{truncate(measure.first_seen_document ?? "—", 34)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </Panel>
  );
}

function Entities({ search, onSearch }: { search: string; onSearch: (value: string) => void }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["entities", search],
    queryFn: () => api.listEntities({ q: search, limit: 500 }),
  });

  const entities = data?.entities ?? [];

  return (
    <Panel flush>
      <div className="toolbar">
        <Field label="Search">
          <input
            className="input"
            type="search"
            placeholder="entity name"
            value={search}
            onChange={(event) => onSearch(event.target.value)}
            style={{ width: 200 }}
          />
        </Field>
        <div className="toolbar__spacer" />
        <span className="meta">{formatNumber(data?.total ?? 0, 0)} entities</span>
      </div>

      {isLoading ? <Loading /> : null}
      {error ? <ErrorNote error={error} /> : null}
      {!isLoading && !entities.length ? <Empty>No entities registered yet.</Empty> : null}

      {entities.length ? (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Entity</th>
                <th>Type</th>
                <th>Also written as</th>
                <th className="numeric">Facts</th>
                <th>First seen in</th>
              </tr>
            </thead>
            <tbody>
              {entities.map((entity) => (
                <tr key={entity.id}>
                  <td>
                    <Link to={`/facts?entity_id=${entity.id}`}>{entity.name}</Link>
                  </td>
                  <td>{entity.entity_type ? titleCase(entity.entity_type) : "—"}</td>
                  <td>
                    {entity.aliases.length > 1 ? (
                      <div className="chip-row">
                        {entity.aliases
                          .filter((alias) => alias !== entity.name)
                          .slice(0, 4)
                          .map((alias) => (
                            <Chip key={alias}>{truncate(alias, 34)}</Chip>
                          ))}
                      </div>
                    ) : (
                      <span className="meta">—</span>
                    )}
                  </td>
                  <td className="numeric">{formatNumber(entity.fact_count, 0)}</td>
                  <td className="meta">{truncate(entity.first_seen_document ?? "—", 34)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </Panel>
  );
}

function Qualifiers() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["qualifiers"],
    queryFn: api.listQualifiers,
  });

  const qualifiers = data?.qualifiers ?? [];

  return (
    <Panel flush>
      <div className="panel__body">
        <p className="meta">
          Qualifier keys are the axes along which two facts can differ without disagreeing.
          Each one discovered here is a distinction the reconciler can use to explain a gap
          rather than report a contradiction.
        </p>
      </div>

      {isLoading ? <Loading /> : null}
      {error ? <ErrorNote error={error} /> : null}
      {!isLoading && !qualifiers.length ? (
        <Empty>No qualifier dimensions discovered yet.</Empty>
      ) : null}

      {qualifiers.length ? (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Dimension</th>
                <th>Observed values</th>
                <th className="numeric">Distinct</th>
                <th className="numeric">Facts</th>
                <th>First seen in</th>
              </tr>
            </thead>
            <tbody>
              {qualifiers.map((qualifier) => (
                <tr key={qualifier.key}>
                  <td>{qualifier.key}</td>
                  <td>
                    <div className="chip-row">
                      {qualifier.values.slice(0, 8).map((value) => (
                        <Chip key={value}>{truncate(value, 28)}</Chip>
                      ))}
                      {qualifier.values.length > 8 ? (
                        <span className="meta">+{qualifier.values.length - 8} more</span>
                      ) : null}
                    </div>
                  </td>
                  <td className="numeric">{qualifier.value_count}</td>
                  <td className="numeric">{formatNumber(qualifier.fact_count, 0)}</td>
                  <td className="meta">{truncate(qualifier.first_seen_document ?? "—", 34)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </Panel>
  );
}
