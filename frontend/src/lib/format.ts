import type { Fact, Relation, RelationType } from "./types";

export const RELATION_LABELS: Record<RelationType, string> = {
  corroborates: "Corroborates",
  contradicts: "Contradicts",
  reconciled_by_context: "Reconciled by context",
  refines: "Refines",
  supersedes: "Supersedes",
};

export const DIMENSION_LABELS: Record<string, string> = {
  period: "Period",
  unit_scale: "Unit scale",
  currency: "Currency",
  scope: "Scope",
  segment: "Segment",
  basis: "Basis",
  vintage: "Data vintage",
  entity: "Entity",
  definition: "Definition",
  none: "—",
};

export const PAGE_TYPE_LABELS: Record<string, string> = {
  prose: "Prose",
  table: "Table",
  chart_slide: "Chart slide",
  mixed: "Mixed",
  toc: "Contents",
  boilerplate: "Boilerplate",
  empty: "Empty",
};

export function relationLabel(type: string): string {
  return RELATION_LABELS[type as RelationType] ?? type;
}

export function dimensionLabel(dimension: string): string {
  return DIMENSION_LABELS[dimension] ?? dimension.replace(/_/g, " ");
}

export function formatNumber(value: number | null | undefined, maximumFractionDigits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString(undefined, { maximumFractionDigits });
}

export function formatPercent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatBytes(bytes: number): string {
  if (!bytes) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

/** Period as a reader would cite it: the document's own label, with the resolved span behind it. */
export function formatPeriod(fact: Pick<Fact, "period_label" | "period_start" | "period_end">): string {
  if (fact.period_label) return fact.period_label;
  if (fact.period_start && fact.period_end) {
    return fact.period_start === fact.period_end
      ? fact.period_start
      : `${fact.period_start} to ${fact.period_end}`;
  }
  return "—";
}

export function formatPeriodSpan(
  fact: Pick<Fact, "period_start" | "period_end">,
): string | null {
  if (!fact.period_start || !fact.period_end) return null;
  if (fact.period_start === fact.period_end) return fact.period_start;
  return `${fact.period_start} → ${fact.period_end}`;
}

export function formatValue(fact: Pick<Fact, "value_text" | "unit_surface">): string {
  if (!fact.value_text) return "—";
  const unit = fact.unit_surface?.trim();
  if (unit && !fact.value_text.toLowerCase().includes(unit.toLowerCase())) {
    return `${fact.value_text} ${unit}`;
  }
  return fact.value_text;
}

/**
 * The normalised value, written compactly.
 *
 * Base units are frequently enormous (rupees, not crore), so a plain toLocaleString would
 * produce a twelve-digit number that tells a reader nothing. Compact notation keeps the
 * magnitude legible while the exact figure stays available in the value column.
 */
export function formatBaseValue(fact: Pick<Fact, "value_base" | "unit_class" | "currency">): string {
  if (fact.value_base === null || fact.value_base === undefined) return "—";
  const magnitude = Math.abs(fact.value_base);
  const compact =
    magnitude >= 10_000
      ? fact.value_base.toLocaleString(undefined, {
          notation: "compact",
          maximumFractionDigits: 3,
        })
      : fact.value_base.toLocaleString(undefined, { maximumFractionDigits: 4 });

  if (fact.unit_class === "currency" && fact.currency) return `${compact} ${fact.currency}`;
  if (fact.unit_class === "ratio") return `${compact}%`;
  if (fact.unit_class === "ratio_change") return `${compact} pp`;
  return compact;
}

export function factLocation(fact: Fact): string {
  const page = fact.source.page_number;
  if (!page) return fact.source.document_title ?? "—";
  const printed = fact.source.printed_label;
  return printed && printed !== String(page) ? `p. ${page} (printed ${printed})` : `p. ${page}`;
}

export function relationTone(relation: Pick<Relation, "type">): string {
  return `chip chip--${relation.type}`;
}

export function truncate(text: string, limit: number): string {
  return text.length <= limit ? text : `${text.slice(0, limit - 1).trimEnd()}…`;
}

export function titleCase(text: string): string {
  return text.replace(/_/g, " ").replace(/^./, (character) => character.toUpperCase());
}
