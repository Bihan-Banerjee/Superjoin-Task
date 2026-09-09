import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

export function Panel({
  title,
  actions,
  flush,
  children,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  flush?: boolean;
  children: ReactNode;
}) {
  return (
    <section className="panel">
      {title || actions ? (
        <header className="panel__header">
          <h2 className="panel__title">{title}</h2>
          {actions ? <div className="panel__actions">{actions}</div> : null}
        </header>
      ) : null}
      <div className={flush ? "panel__body panel__body--flush" : "panel__body"}>{children}</div>
    </section>
  );
}

export function Field({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span className="field__label">{label}</span>
      {children}
    </label>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="empty">
      <span className="spinner" aria-hidden /> {label}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : String(error);
  return <div className="error-note">{message}</div>;
}

export function Chip({
  tone,
  title,
  children,
}: {
  tone?: string;
  title?: string;
  children: ReactNode;
}) {
  return (
    <span className={tone ? `chip chip--${tone}` : "chip"} title={title}>
      {children}
    </span>
  );
}

export function MetaList({ items }: { items: [string, ReactNode][] }) {
  const present = items.filter(([, value]) => value !== null && value !== undefined && value !== "");
  if (!present.length) return null;
  return (
    <dl className="meta-list">
      {present.map(([label, value]) => (
        <div key={label} style={{ display: "contents" }}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * A quote with the matched value highlighted.
 *
 * Highlighting is done by locating the value string in the quote rather than by storing
 * offsets, because the quote shown here is already the span the backend verified: the
 * value is guaranteed to be inside it.
 */
export function Quote({ text, highlight }: { text: string; highlight?: string | null }) {
  if (!highlight) return <blockquote className="quote">{text}</blockquote>;

  const index = text.toLowerCase().indexOf(highlight.toLowerCase());
  if (index < 0) return <blockquote className="quote">{text}</blockquote>;

  return (
    <blockquote className="quote">
      {text.slice(0, index)}
      <mark>{text.slice(index, index + highlight.length)}</mark>
      {text.slice(index + highlight.length)}
    </blockquote>
  );
}

export function Progress({ value }: { value: number }) {
  return (
    <div
      className="progress"
      role="progressbar"
      aria-valuenow={Math.round(value * 100)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div className="progress__bar" style={{ width: `${Math.min(100, value * 100)}%` }} />
    </div>
  );
}

export function Pagination({
  total,
  limit,
  offset,
  onChange,
}: {
  total: number;
  limit: number;
  offset: number;
  onChange: (offset: number) => void;
}) {
  if (total <= limit) return null;
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.ceil(total / limit);
  return (
    <div className="pagination">
      <button
        type="button"
        className="btn btn--sm"
        disabled={offset === 0}
        onClick={() => onChange(Math.max(0, offset - limit))}
      >
        Previous
      </button>
      <span className="meta">
        Page {page} of {pages} · {total.toLocaleString()} results
      </span>
      <button
        type="button"
        className="btn btn--sm"
        disabled={offset + limit >= total}
        onClick={() => onChange(offset + limit)}
      >
        Next
      </button>
    </div>
  );
}

/**
 * Download the current view as a file.
 *
 * The filters in the URL are passed through, so what lands in the spreadsheet is what is on
 * screen rather than the whole corpus: otherwise the export answers a different question
 * from the one the reader was asking.
 */
export function ExportLinks({
  resource,
  params,
}: {
  resource: "facts" | "relations";
  params?: Record<string, string | number | null | undefined>;
}) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value === null || value === undefined || value === "") continue;
    search.set(key, String(value));
  }
  const suffix = search.toString();
  const url = (format: string) =>
    `/api/export/${resource}?format=${format}${suffix ? `&${suffix}` : ""}`;

  return (
    <span className="export-links">
      <span className="meta">Export</span>
      <a className="btn btn--sm" href={url("csv")} download>
        CSV
      </a>
      <a className="btn btn--sm" href={url("json")} download>
        JSON
      </a>
    </span>
  );
}

/**
 * A long list, shown short.
 *
 * Several pages here produce lists whose length is data-dependent: rejection reasons,
 * measure aliases, selection rationales, facts on a page. Rendering all of them pushes
 * everything below off the screen, and truncating without a way back hides evidence, which
 * this interface should never do. So the tail is collapsed rather than dropped, and the
 * control says how much is behind it.
 */
export function Expandable({
  items,
  initial = 5,
  noun = "more",
  children,
}: {
  items: ReactNode[];
  initial?: number;
  noun?: string;
  children?: (item: ReactNode, index: number) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const hidden = Math.max(0, items.length - initial);
  const shown = open ? items : items.slice(0, initial);

  return (
    <>
      {shown.map((item, index) => (children ? children(item, index) : item))}
      {hidden > 0 ? (
        <button
          type="button"
          className="expander"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
        >
          {open ? "Show fewer" : `Show ${hidden} ${noun}`}
          <span aria-hidden="true" className="expander__mark">
            {open ? "−" : "+"}
          </span>
        </button>
      ) : null}
    </>
  );
}

/**
 * A block of content that is capped in height until asked to open.
 *
 * Used where the content is one long thing rather than a list: a page of prose, a wide
 * table: so slicing items is not available. The fade at the cut edge is the affordance:
 * it says the text continues rather than ends.
 */
export function ExpandableBlock({
  children,
  maxHeight = 260,
  label = "Show the rest",
}: {
  children: ReactNode;
  maxHeight?: number;
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  const [overflows, setOverflows] = useState(false);
  const inner = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = inner.current;
    if (!element) return;
    // Only offer the control when there is genuinely something hidden; otherwise every
    // short block grows a button that does nothing.
    const check = () => setOverflows(element.scrollHeight > maxHeight + 8);
    check();
    const observer = new ResizeObserver(check);
    observer.observe(element);
    return () => observer.disconnect();
  }, [maxHeight, children]);

  return (
    <div className="clamp">
      <div
        ref={inner}
        className={overflows && !open ? "clamp__body clamp__body--cut" : "clamp__body"}
        style={overflows && !open ? { maxHeight } : undefined}
      >
        {children}
      </div>
      {overflows ? (
        <button type="button" className="expander" onClick={() => setOpen((value) => !value)}>
          {open ? "Collapse" : label}
          <span aria-hidden="true" className="expander__mark">
            {open ? "−" : "+"}
          </span>
        </button>
      ) : null}
    </div>
  );
}
