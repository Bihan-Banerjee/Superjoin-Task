import type {
  CaseBlock,
  DocumentDetail,
  DocumentSummary,
  Entity,
  Evaluation,
  Fact,
  FactDetail,
  Health,
  Job,
  Measure,
  QualifierKey,
  Rejection,
  Relation,
  RelationGraphData,
  SettingsPayload,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type Params = Record<string, string | number | boolean | null | undefined>;

function query(params: Params = {}): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") continue;
    search.set(key, String(value));
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    // FastAPI reports failures as {detail}; anything else is surfaced verbatim so an
    // unexpected error is not flattened into a generic message.
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") message = body.detail;
      else if (Array.isArray(body?.detail)) message = body.detail.map((d: { msg?: string }) => d.msg).join(", ");
    } catch {
      /* the body was not JSON */
    }
    throw new ApiError(message, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  health: () => request<Health>("/api/health"),

  getSettings: () => request<SettingsPayload>("/api/settings"),
  updateSettings: (changes: Record<string, unknown>) =>
    request<{ values: Record<string, unknown>; warnings: string[] }>("/api/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(changes),
    }),

  listDocuments: () => request<{ documents: DocumentSummary[] }>("/api/documents"),
  getDocument: (id: number) => request<DocumentDetail>(`/api/documents/${id}`),
  deleteDocument: (id: number) => request<void>(`/api/documents/${id}`, { method: "DELETE" }),
  reprocessDocument: (id: number) =>
    request<{ job: Job }>(`/api/documents/${id}/reprocess`, { method: "POST" }),

  uploadDocument: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<{
      document: DocumentSummary;
      job: Job | null;
      duplicate: boolean;
      message?: string;
    }>("/api/documents", { method: "POST", body });
  },

  listFacts: (params: Params = {}) =>
    request<{ total: number; limit: number; offset: number; facts: Fact[] }>(
      `/api/facts${query(params)}`,
    ),
  getFact: (id: number) => request<FactDetail>(`/api/facts/${id}`),

  listRelations: (params: Params = {}) =>
    request<{
      total: number;
      limit: number;
      offset: number;
      relations: Relation[];
      counts: Record<string, number>;
    }>(`/api/relations${query(params)}`),
  getRelation: (id: number) => request<Relation>(`/api/relations/${id}`),
  relationGraph: (params: Params = {}) =>
    request<RelationGraphData>(`/api/relations/graph${query(params)}`),

  listMeasures: (params: Params = {}) =>
    request<{
      total: number;
      measures: Measure[];
      unit_classes: { unit_class: string | null; count: number }[];
    }>(`/api/measures${query(params)}`),
  listEntities: (params: Params = {}) =>
    request<{ total: number; entities: Entity[] }>(`/api/entities${query(params)}`),
  listQualifiers: () => request<{ qualifiers: QualifierKey[] }>("/api/qualifiers"),

  getCases: () => request<{ cases: CaseBlock[] }>("/api/cases"),
  getEvaluation: () => request<Evaluation>("/api/evaluation"),
  listRejections: (params: Params = {}) =>
    request<{ total: number; limit: number; offset: number; rejections: Rejection[] }>(
      `/api/evaluation/rejections${query(params)}`,
    ),

  listJobs: (params: Params = {}) => request<{ jobs: Job[] }>(`/api/jobs${query(params)}`),
  getJob: (id: number) => request<Job>(`/api/jobs/${id}`),

  pageImageUrl: (documentId: number, pageNumber: number, dpi = 150) =>
    `/api/documents/${documentId}/pages/${pageNumber}/image?dpi=${dpi}`,
  documentFileUrl: (documentId: number) => `/api/documents/${documentId}/file`,
};

/**
 * Subscribe to a job's progress.
 *
 * Falls back to polling when EventSource is unavailable or the stream errors, because a
 * proxy that buffers server-sent events would otherwise leave the upload looking frozen
 * for the whole ingest.
 */
export function watchJob(
  jobId: number,
  onUpdate: (job: Job) => void,
  onDone: (job: Job) => void,
): () => void {
  let closed = false;
  let source: EventSource | null = null;
  let timer: number | null = null;

  const finish = (job: Job) => {
    if (closed) return;
    closed = true;
    source?.close();
    if (timer !== null) window.clearInterval(timer);
    onDone(job);
  };

  const poll = () => {
    timer = window.setInterval(async () => {
      try {
        const job = await api.getJob(jobId);
        onUpdate(job);
        if (job.stage === "done" || job.stage === "failed") finish(job);
      } catch {
        /* keep polling; a transient failure should not end the subscription */
      }
    }, 1200);
  };

  if (typeof EventSource === "undefined") {
    poll();
  } else {
    source = new EventSource(`/api/jobs/${jobId}/stream`);
    source.onmessage = (event) => {
      const job = JSON.parse(event.data) as Job;
      onUpdate(job);
      if (job.stage === "done" || job.stage === "failed") finish(job);
    };
    source.onerror = () => {
      if (closed) return;
      source?.close();
      source = null;
      poll();
    };
  }

  return () => {
    closed = true;
    source?.close();
    if (timer !== null) window.clearInterval(timer);
  };
}
