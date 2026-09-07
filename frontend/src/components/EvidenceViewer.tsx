import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "../lib/api";
import type { Fact } from "../lib/types";
import { Quote } from "./primitives";

/**
 * Shows a fact's evidence on the page it came from.
 *
 * The page is rendered server-side by PyMuPDF rather than in the browser by pdf.js. That
 * choice costs a round trip, and buys three things: the highlight rectangles are in the
 * same coordinate space as the image by construction, a hundred-megabyte filing is never
 * shipped to the client to show one page, and the viewer works identically for a document
 * the browser could not parse.
 *
 * Rectangles arrive in PDF points from the backend. They are positioned as percentages of
 * the page box, so they stay aligned at any rendered width without recomputing anything.
 */
export default function EvidenceViewer({ fact }: { fact: Fact }) {
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const [zoomed, setZoomed] = useState(false);
  const highlightRef = useRef<HTMLDivElement>(null);

  const { document_id: documentId, page_number: pageNumber } = fact.source;
  const pageWidth = fact.source.page_width ?? 0;
  const pageHeight = fact.source.page_height ?? 0;

  const boxes = useMemo(() => {
    if (!pageWidth || !pageHeight) return [];
    return fact.evidence.bboxes
      .filter((box): box is [number, number, number, number] => box.length === 4)
      .map((box) => {
        const [x0, y0, x1, y1] = box;
        return {
          left: (x0 / pageWidth) * 100,
          top: (y0 / pageHeight) * 100,
          width: ((x1 - x0) / pageWidth) * 100,
          height: ((y1 - y0) / pageHeight) * 100,
        };
      });
  }, [fact.evidence.bboxes, pageWidth, pageHeight]);

  useEffect(() => {
    setLoaded(false);
    setFailed(false);
  }, [documentId, pageNumber]);

  // Bring the highlight into view once the page image has painted, so a fact three
  // quarters of the way down a page does not open off screen.
  useEffect(() => {
    if (!loaded || !highlightRef.current) return;
    highlightRef.current.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [loaded]);

  if (!documentId || !pageNumber) {
    return <div className="empty">This fact has no page reference.</div>;
  }

  return (
    <div className="evidence">
      <div className="evidence__bar">
        <span className="meta">
          {fact.source.document_title} · page {pageNumber}
          {fact.source.printed_label && fact.source.printed_label !== String(pageNumber)
            ? ` (printed ${fact.source.printed_label})`
            : ""}
        </span>
        <div className="evidence__actions">
          <button type="button" className="btn btn--sm" onClick={() => setZoomed((value) => !value)}>
            {zoomed ? "Fit width" : "Zoom"}
          </button>
          <a
            className="btn btn--sm"
            href={api.documentFileUrl(documentId)}
            target="_blank"
            rel="noreferrer"
          >
            Open PDF
          </a>
        </div>
      </div>

      <div className={zoomed ? "evidence__scroll evidence__scroll--zoomed" : "evidence__scroll"}>
        <div className="evidence__page">
          {!loaded && !failed ? (
            <div className="evidence__placeholder">
              <span className="spinner" aria-hidden /> Rendering page
            </div>
          ) : null}
          {failed ? (
            <div className="evidence__placeholder">Could not render this page.</div>
          ) : (
            <img
              src={api.pageImageUrl(documentId, pageNumber, 150)}
              alt={`Page ${pageNumber}`}
              onLoad={() => setLoaded(true)}
              onError={() => setFailed(true)}
              draggable={false}
            />
          )}

          {loaded
            ? boxes.map((box, index) => (
                <div
                  key={index}
                  ref={index === 0 ? highlightRef : undefined}
                  className="evidence__highlight"
                  style={{
                    left: `${box.left}%`,
                    top: `${box.top}%`,
                    width: `${box.width}%`,
                    height: `${box.height}%`,
                  }}
                />
              ))
            : null}
        </div>
      </div>

      {boxes.length === 0 ? (
        <p className="meta evidence__note">
          The quote was verified in the page text but could not be located as a rectangle,
          usually because it spans a column break. The text is shown below instead.
        </p>
      ) : null}

      <Quote text={fact.evidence.quote} highlight={fact.value_text} />
    </div>
  );
}
