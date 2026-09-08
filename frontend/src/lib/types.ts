export type DocumentStatus = "pending" | "processing" | "ready" | "failed";

export interface DocumentSummary {
  id: number;
  filename: string;
  title: string;
  publisher: string | null;
  doc_type: string | null;
  subject_entity: string | null;
  as_of_date: string | null;
  published_date: string | null;
  period_label: string | null;
  default_currency: string | null;
  default_scale: number | null;
  fiscal_convention: string;
  reporting_basis: string | null;
  page_count: number;
  byte_size: number;
  status: DocumentStatus;
  error: string | null;
  sha256: string;
  near_duplicate_of: number | null;
  content_overlap: number | null;
  created_at: string | null;
  fact_count: number;
  relation_count: number;
  rejection_count: number;
}

export interface PageSummary {
  page_number: number;
  printed_label: string | null;
  page_type: string;
  word_count: number;
  extracted: boolean;
  unit_currency: string | null;
  unit_scale: number | null;
  reason: string | null;
}

export interface DocumentDetail extends DocumentSummary {
  profile: Record<string, unknown>;
  pages: PageSummary[];
}

export interface Evidence {
  quote: string;
  start: number | null;
  end: number | null;
  score: number;
  exact: boolean;
  bboxes: number[][];
}

export interface FactSource {
  document_id: number;
  document_title: string | null;
  publisher: string | null;
  page_number: number | null;
  printed_label: string | null;
  page_width: number | null;
  page_height: number | null;
}

export interface Fact {
  id: number;
  kind: string;
  statement: string;
  subject: string;
  predicate: string;
  measure_id: number | null;
  measure: string | null;
  entity_id: number | null;
  entity: string | null;
  value_text: string | null;
  value_number: number | null;
  value_base: number | null;
  unit_surface: string | null;
  unit: string | null;
  unit_class: string | null;
  currency: string | null;
  period_label: string | null;
  period_kind: string | null;
  period_start: string | null;
  period_end: string | null;
  qualifiers: Record<string, string>;
  basis: string | null;
  direction: string | null;
  confidence: number;
  evidence: Evidence;
  source: FactSource;
}

export interface FactDetail extends Fact {
  extractor: string;
  model: string | null;
  issues: string[];
  used_vision: boolean;
  relations: Relation[];
}

export type RelationType =
  | "corroborates"
  | "contradicts"
  | "reconciled_by_context"
  | "refines"
  | "supersedes";

export interface Relation {
  id: number;
  type: RelationType;
  subtype: string | null;
  dimension: string;
  explanation: string;
  decided_by: "rule" | "model";
  rule_id: string | null;
  confidence: number;
  severity: number;
  delta_absolute: number | null;
  delta_relative: number | null;
  cross_document: boolean;
  similarity: number | null;
  superseded_fact_id: number | null;
  order_sensitive: boolean;
  reverse_relation_type: RelationType | null;
  left: Fact;
  right: Fact;
  selected_because?: string[];
}

export interface Measure {
  id: number;
  slug: string;
  name: string;
  description: string | null;
  unit_class: string | null;
  aliases: string[];
  alias_count: number;
  fact_count: number;
  document_count: number;
  first_seen_document_id: number | null;
  first_seen_document: string | null;
  created_at: string | null;
}

export interface Entity {
  id: number;
  slug: string;
  name: string;
  entity_type: string | null;
  aliases: string[];
  fact_count: number;
  first_seen_document: string | null;
}

export interface QualifierKey {
  key: string;
  values: string[];
  value_count: number;
  fact_count: number;
  discriminating: boolean;
  first_seen_document: string | null;
}

export interface Rejection {
  id: number;
  document_id: number;
  document_title: string | null;
  page_number: number | null;
  stage: string;
  reason: string;
  detail: string;
  candidate: Record<string, unknown>;
  created_at: string | null;
}

export interface Job {
  id: number;
  document_id: number | null;
  stage: string;
  progress: number;
  message: string;
  error: string | null;
  stats: Record<string, unknown>;
  started_at: string | null;
  finished_at: string | null;
}

export interface CaseBlock {
  key: "corroboration" | "contradiction" | "reconciled" | "failure";
  title: string;
  description: string;
  found: boolean;
  examples: Relation[] | Rejection[];
  summary?: {
    facts_kept: number;
    candidates_rejected: number;
    grounding_pass_rate: number;
  };
  by_reason?: { reason: string; count: number; explanation: string }[];
}

export interface Health {
  status: string;
  provider: string;
  provider_configured: boolean;
  fallback_provider: string | null;
  vision_enabled: boolean;
  embedding_model: string;
  cache_enabled: boolean;
  concurrency: number;
  rate_limit_rpm: number;
  throttle_warnings: string[];
  graph_view_enabled: boolean;
  graph_view_warning: string | null;
}

export interface GraphNode {
  id: number;
  label: string;
  value_text: string | null;
  period: string | null;
  document_id: number;
  measure_id: number | null;
  measure: string | null;
  degree: number;
}

export interface GraphEdge {
  id: number;
  source: number;
  target: number;
  type: RelationType;
  dimension: string;
  cross_document: boolean;
  severity: number;
  superseded_fact_id: number | null;
}

export interface RelationGraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  total_relations: number;
  truncated: boolean;
  documents: { id: number; title: string }[];
}

export interface Evaluation {
  corpus: {
    documents: number;
    pages: number;
    pages_extracted: number;
    page_types: { page_type: string; count: number }[];
  };
  grounding: {
    candidates_proposed: number;
    facts_kept: number;
    candidates_rejected: number;
    pass_rate: number;
    quotes_matched_exactly: number;
    exact_match_rate: number;
    facts_located_on_page: number;
    page_location_rate: number;
  };
  rejections_by_reason: { reason: string; count: number }[];
  coverage: {
    document_id: number;
    title: string;
    page_count: number;
    pages_extracted: number;
    facts: number;
    rejections: number;
    facts_per_extracted_page: number;
  }[];
  relations: {
    total: number;
    cross_document: number;
    by_type: { type: string; count: number }[];
    by_dimension: { dimension: string; count: number }[];
    by_decision: { decided_by: string; count: number }[];
    by_rule: { rule_id: string; count: number }[];
    adjudication: {
      decided_by_model: number;
      order_sensitive: number;
      order_sensitive_rate: number;
    };
  };
  registry: {
    measures: number;
    measures_in_multiple_documents: number;
    shared_measure_rate: number;
    entities: number;
    qualifier_keys: number;
  };
  normalisation: {
    facts: number;
    numeric: number;
    unit_resolved: number;
    unit_resolution_rate: number;
    period_resolved: number;
    period_resolution_rate: number;
    measure_assigned: number;
    measure_assignment_rate: number;
    by_unit_class: { unit_class: string; count: number }[];
  };
  model_usage: {
    calls: number;
    billed_calls: number;
    cache_hits: number;
    cache_hit_rate: number;
    failed_calls: number;
    input_tokens: number;
    output_tokens: number;
    median_latency_ms: number;
    by_purpose: { purpose: string; count: number }[];
  };
}

export interface Paged<T> {
  total: number;
  limit: number;
  offset: number;
  facts?: T[];
  relations?: T[];
  rejections?: T[];
}
