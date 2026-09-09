import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { Empty, ErrorNote, Loading, Panel, Progress } from "../components/primitives";
import { api, watchJob } from "../lib/api";
import {
  formatBytes,
  formatDate,
  formatNumber,
  formatPercent,
  titleCase,
} from "../lib/format";
import type { DocumentSummary, Job } from "../lib/types";

interface ActiveJob {
  job: Job;
  filename: string;
}

export default function Documents() {
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [active, setActive] = useState<Record<number, ActiveJob>>({});
  const [notice, setNotice] = useState<string | null>(null);
  const unsubscribers = useRef<(() => void)[]>([]);

  const { data, isLoading, error } = useQuery({
    queryKey: ["documents"],
    queryFn: api.listDocuments,
    // Poll while anything is mid-ingest so status and counts settle without a reload.
    refetchInterval: (query) =>
      query.state.data?.documents.some((d) => d.status === "processing" || d.status === "pending")
        ? 3000
        : false,
  });

  useEffect(() => {
    const list = unsubscribers.current;
    return () => list.forEach((stop) => stop());
  }, []);

  const track = useCallback(
    (job: Job, filename: string) => {
      setActive((current) => ({ ...current, [job.id]: { job, filename } }));
      const stop = watchJob(
        job.id,
        (update) =>
          setActive((current) => ({ ...current, [update.id]: { job: update, filename } })),
        (final) => {
          setActive((current) => {
            const next = { ...current };
            delete next[final.id];
            return next;
          });
          queryClient.invalidateQueries();
        },
      );
      unsubscribers.current.push(stop);
    },
    [queryClient],
  );

  const upload = useMutation({
    mutationFn: api.uploadDocument,
    onSuccess: (result) => {
      if (result.duplicate) {
        setNotice(
          `${result.document.filename} is already in the knowledge layer: the same file content was uploaded before.`,
        );
        return;
      }
      setNotice(null);
      if (result.job) track(result.job, result.document.filename);
      queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
  });

  const reprocess = useMutation({
    mutationFn: api.reprocessDocument,
    onSuccess: (result, documentId) => {
      const doc = data?.documents.find((item) => item.id === documentId);
      track(result.job, doc?.filename ?? "document");
    },
  });

  const remove = useMutation({
    mutationFn: api.deleteDocument,
    onSuccess: () => queryClient.invalidateQueries(),
  });

  const submit = (files: FileList | null) => {
    if (!files?.length) return;
    setNotice(null);
    Array.from(files).forEach((file) => upload.mutate(file));
  };

  const { data: health } = useQuery({ queryKey: ["health"], queryFn: api.health });
  const readOnly = Boolean(health?.read_only);

  const documents = data?.documents ?? [];
  const running = Object.values(active);

  return (
    <div className="page">
      {health?.throttle_warnings.length ? (
        <div className="warning-note">
          <strong>Rate limit warning</strong>
          <ul>
            {health.throttle_warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
          <p className="meta">
            Ingest will still run: requests that are rejected are retried with backoff.
            Lower <code>LLM_RATE_LIMIT_RPM</code> and <code>LLM_CONCURRENCY</code> in{" "}
            <code>backend/.env</code> to avoid it.
          </p>
        </div>
      ) : null}
      <p className="page__intro">
        Upload a PDF and it is parsed, read page by page, and compared against everything
        already here. Nothing is specific to the sample corpus: the conventions each
        document follows are read from the document itself.
      </p>

      {readOnly ? (
        <p className="graph__notice">
          This deployment is read-only. It serves a prepared snapshot, so documents cannot be
          uploaded, reprocessed or deleted: and no model key is needed or present.
        </p>
      ) : (
      <div
        className={dragging ? "dropzone dropzone--active" : "dropzone"}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          submit(event.dataTransfer.files);
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf"
          multiple
          hidden
          onChange={(event) => {
            submit(event.target.files);
            event.target.value = "";
          }}
        />
        <p>Drop PDFs here, or</p>
        <button
          type="button"
          className="btn btn--primary"
          onClick={() => inputRef.current?.click()}
          disabled={upload.isPending}
        >
          {upload.isPending ? "Uploading" : "Choose files"}
        </button>
      </div>
      )}

      {notice ? <p className="meta dropzone__notice">{notice}</p> : null}
      {upload.error ? <ErrorNote error={upload.error} /> : null}

      {running.length ? (
        <div className="job-list">
          {running.map(({ job, filename }) => (
            <div key={job.id} className="job">
              <div className="job__head">
                <span className="job__name">{filename}</span>
                <span className="meta">
                  {titleCase(job.stage)} · {Math.round(job.progress * 100)}%
                </span>
              </div>
              <Progress value={job.progress} />
              {job.message ? <span className="meta">{job.message}</span> : null}
              {job.error ? <ErrorNote error={job.error} /> : null}
            </div>
          ))}
        </div>
      ) : null}

      <Panel title={`Documents (${documents.length})`} flush>
        {isLoading ? <Loading /> : null}
        {error ? <ErrorNote error={error} /> : null}
        {!isLoading && !documents.length ? (
          <Empty>Nothing ingested yet. Upload a PDF to begin.</Empty>
        ) : null}

        {documents.length ? (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Document</th>
                  <th>Publisher</th>
                  <th>Type</th>
                  <th>Period</th>
                  <th>Denomination</th>
                  <th className="numeric">Pages</th>
                  <th className="numeric">Facts</th>
                  <th className="numeric">Relations</th>
                  <th className="numeric">Rejected</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {documents.map((document) => (
                  <DocumentRow
                    key={document.id}
                    document={document}
                    repeats={documents.find((other) => other.id === document.near_duplicate_of)}
                    readOnly={readOnly}
                    onReprocess={() => reprocess.mutate(document.id)}
                    onDelete={() => {
                      if (
                        window.confirm(
                          `Remove ${document.title}? Its facts and every relation touching them are deleted.`,
                        )
                      ) {
                        remove.mutate(document.id);
                      }
                    }}
                    busy={reprocess.isPending || remove.isPending}
                  />
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </Panel>
    </div>
  );
}

function DocumentRow({
  document,
  repeats,
  readOnly,
  onReprocess,
  onDelete,
  busy,
}: {
  document: DocumentSummary;
  repeats?: DocumentSummary;
  readOnly: boolean;
  onReprocess: () => void;
  onDelete: () => void;
  busy: boolean;
}) {
  const denomination = [document.default_currency, scaleName(document.default_scale)]
    .filter(Boolean)
    .join(" ");

  return (
    <tr>
      <td>
        <Link to={`/facts?document_id=${document.id}`} title={document.filename}>
          {document.title}
        </Link>
        <div className="meta">
          {formatBytes(document.byte_size)} · added {formatDate(document.created_at)}
        </div>
        {document.scanned_pages > 0 ? (
          <div className="meta" title={SCANNED_HINT}>
            {document.scanned_pages} page(s) had no text layer
            {document.ocr_pages > 0 ? `, ${document.ocr_pages} read by OCR` : ""}
          </div>
        ) : null}
        {repeats ? (
          <div className="meta" title={REPEAT_HINT}>
            Repeats {formatPercent(document.content_overlap ?? 0)} of {repeats.title}
          </div>
        ) : null}
      </td>
      <td>{document.publisher ?? "-"}</td>
      <td>{document.doc_type ? titleCase(document.doc_type) : "-"}</td>
      <td>{document.period_label ?? "-"}</td>
      <td>
        {denomination || "-"}
        <div className="meta">{titleCase(document.fiscal_convention)} year</div>
      </td>
      <td className="numeric">{formatNumber(document.page_count, 0)}</td>
      <td className="numeric">{formatNumber(document.fact_count, 0)}</td>
      <td className="numeric">{formatNumber(document.relation_count, 0)}</td>
      <td className="numeric">{formatNumber(document.rejection_count, 0)}</td>
      <td>
        <span className={`chip chip--status-${document.status}`}>{titleCase(document.status)}</span>
        {document.error ? <div className="meta">{document.error}</div> : null}
      </td>
      <td>
        {readOnly ? (
          <span className="meta">Read-only</span>
        ) : (
          <div className="row-actions">
            <button type="button" className="btn btn--sm" onClick={onReprocess} disabled={busy}>
              Reprocess
            </button>
            <button
              type="button"
              className="btn btn--sm btn--danger"
              onClick={onDelete}
              disabled={busy}
            >
              Delete
            </button>
          </div>
        )}
      </td>
    </tr>
  );
}

// Matched on page text rather than on the file, so the same content re-exported or
// re-downloaded is caught even though its bytes differ.
const REPEAT_HINT =
  "Most of this document's text was already in the layer when it was added. " +
  "Nothing was skipped: the pages that differ are still the reason to keep it.";

const SCANNED_HINT =
  "These pages carry an image with no text behind it. Facts cannot be extracted from them " +
  "unless OCR is enabled with ENABLE_OCR=true.";

function scaleName(scale: number | null): string {
  if (!scale || scale === 1) return "";
  const names: [number, string][] = [
    [1e12, "trillion"],
    [1e9, "billion"],
    [1e7, "crore"],
    [1e6, "million"],
    [1e5, "lakh"],
    [1e3, "thousand"],
  ];
  return names.find(([factor]) => Math.abs(scale - factor) < 1)?.[1] ?? "";
}
