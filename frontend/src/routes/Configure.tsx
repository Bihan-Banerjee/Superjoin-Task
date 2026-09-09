import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Empty, ErrorNote, Loading, Panel } from "../components/primitives";
import { api } from "../lib/api";
import type { SettingField, SettingsPayload } from "../lib/types";

/**
 * Settings, editable in place.
 *
 * The fields are described by the server rather than listed here, so adding a setting is a
 * backend change and the two cannot drift. Secrets are write-only in both directions: the
 * server sends back only whether one is set and its last four characters, and this page
 * never puts a stored key into an input.
 */
export default function Configure() {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [saved, setSaved] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["settings"],
    queryFn: api.getSettings,
  });

  const save = useMutation({
    mutationFn: (changes: Record<string, unknown>) => api.updateSettings(changes),
    onSuccess: () => {
      setDraft({});
      setSaved(new Date().toLocaleTimeString());
      // Health drives the graph toggle and the "no model configured" banner; the evaluation
      // header is unaffected but cheap to leave alone.
      queryClient.invalidateQueries({ queryKey: ["settings"] });
      queryClient.invalidateQueries({ queryKey: ["health"] });
    },
  });

  if (isLoading) return <Loading label="Loading settings" />;
  if (error) return <ConfigureUnavailable error={error} />;
  if (!data) return <Empty>No settings available.</Empty>;

  const groups = groupBy(data.fields);
  const pending = Object.keys(draft).length;

  const valueOf = (field: SettingField): unknown =>
    field.name in draft ? draft[field.name] : data.values[field.name];

  return (
    <div className="page">
      <p className="page__intro">
        Changes are written to <code>{data.env_path}</code> and apply to work started
        afterwards: anything already running keeps the settings it began with. This page is
        served only to the machine the server runs on, and is disabled entirely in read-only
        mode.
      </p>

      {data.warnings.length ? (
        <div className="notice notice--warn">
          <ul>
            {data.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {save.error ? <ErrorNote error={save.error} /> : null}

      {Object.entries(groups).map(([group, fields]) => (
        <Panel key={group} title={group}>
          <div className="settings">
            {fields.map((field) => (
              <Field
                key={field.name}
                field={field}
                value={valueOf(field)}
                onChange={(next) => {
                  setSaved(null);
                  setDraft((current) => ({ ...current, [field.name]: next }));
                }}
              />
            ))}
          </div>
        </Panel>
      ))}

      <div className="settings__actions">
        <button
          type="button"
          className="btn btn--primary"
          disabled={!pending || save.isPending}
          onClick={() => save.mutate(draft)}
        >
          {save.isPending ? "Saving" : pending ? `Save ${pending} change${pending > 1 ? "s" : ""}` : "Saved"}
        </button>
        {pending ? (
          <button type="button" className="btn btn--quiet" onClick={() => setDraft({})}>
            Discard
          </button>
        ) : null}
        {saved ? <span className="meta">Written at {saved}</span> : null}
      </div>
    </div>
  );
}

function Field({
  field,
  value,
  onChange,
}: {
  field: SettingField;
  value: unknown;
  onChange: (next: unknown) => void;
}) {
  const secret = field.kind === "secret";
  const stored = secret ? (value as { configured: boolean; hint: string } | undefined) : undefined;

  return (
    <div className="setting">
      <div className="setting__label">
        <label htmlFor={field.name}>{field.label}</label>
        <p className="setting__help">{field.help}</p>
      </div>

      <div className="setting__control">
        {field.kind === "toggle" ? (
          <button
            id={field.name}
            type="button"
            role="switch"
            aria-checked={Boolean(value)}
            className={value ? "switch switch--on" : "switch"}
            onClick={() => onChange(!value)}
          >
            <span className="switch__knob" />
            <span className="switch__text">{value ? "On" : "Off"}</span>
          </button>
        ) : null}

        {field.kind === "choice" ? (
          <select
            id={field.name}
            className="select"
            value={String(value ?? "")}
            onChange={(event) => onChange(event.target.value)}
          >
            {field.choices?.map((choice) => (
              <option key={choice || "none"} value={choice}>
                {choice || "None"}
              </option>
            ))}
          </select>
        ) : null}

        {field.kind === "number" ? (
          <input
            id={field.name}
            className="input input--number"
            type="number"
            min={0}
            value={String(value ?? "")}
            onChange={(event) => onChange(event.target.value)}
          />
        ) : null}

        {secret ? (
          <div className="setting__secret">
            <input
              id={field.name}
              className="input"
              type="password"
              autoComplete="off"
              spellCheck={false}
              placeholder={
                stored?.configured ? `stored, ending ${stored.hint}` : "not configured"
              }
              value={typeof value === "string" ? value : ""}
              onChange={(event) => onChange(event.target.value)}
            />
            {stored?.configured ? (
              <button type="button" className="btn btn--sm btn--quiet" onClick={() => onChange("")}>
                Clear
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

/** A 403 here is a deliberate posture, not a fault, so it is explained rather than reported. */
function ConfigureUnavailable({ error }: { error: unknown }) {
  const status = (error as { status?: number })?.status;
  if (status === 403) {
    return (
      <div className="page">
        <div className="notice">
          <p>
            Configuration is not available here. Either this deployment is read-only, or the
            page is being opened from a machine other than the one running the server.
          </p>
          <p className="meta">
            Settings can still be edited directly in <code>backend/.env</code>.
          </p>
        </div>
      </div>
    );
  }
  return <ErrorNote error={error} />;
}

function groupBy(fields: SettingField[]): Record<string, SettingField[]> {
  const groups: Record<string, SettingField[]> = {};
  for (const field of fields) {
    (groups[field.group] ??= []).push(field);
  }
  return groups;
}

export type { SettingsPayload };
