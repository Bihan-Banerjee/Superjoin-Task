"""Document upload, listing and page rendering."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.serializers import document_detail, document_summary, job_summary
from app.config import get_settings
from app.core.hashing import sha256_bytes
from app.db.engine import db_session
from app.db.models import (
    DOC_STATUS_PENDING,
    Document,
    Fact,
    Job,
    Page,
    Rejection,
    Relation,
)
from app.pipeline.orchestrator import ingest_document
from app.pipeline.parse import render_page_png

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/documents", tags=["documents"])

CHUNK = 1 << 20


def _counts(session: Session, document_ids: list[int]) -> dict[int, dict[str, int]]:
    if not document_ids:
        return {}
    result: dict[int, dict[str, int]] = {
        document_id: {"facts": 0, "relations": 0, "rejections": 0} for document_id in document_ids
    }

    for document_id, count in session.execute(
        select(Fact.document_id, func.count(Fact.id))
        .where(Fact.document_id.in_(document_ids))
        .group_by(Fact.document_id)
    ):
        result[document_id]["facts"] = int(count)

    for document_id, count in session.execute(
        select(Rejection.document_id, func.count(Rejection.id))
        .where(Rejection.document_id.in_(document_ids))
        .group_by(Rejection.document_id)
    ):
        result[document_id]["rejections"] = int(count)

    # A relation belongs to both of its documents, so it is counted once per side.
    left = Fact.__table__.alias("left_fact")
    right = Fact.__table__.alias("right_fact")
    rows = session.execute(
        select(left.c.document_id, right.c.document_id).select_from(
            Relation.__table__.join(left, Relation.left_fact_id == left.c.id).join(
                right, Relation.right_fact_id == right.c.id
            )
        )
    ).all()
    for left_document, right_document in rows:
        for document_id in {left_document, right_document}:
            if document_id in result:
                result[document_id]["relations"] += 1
    return result


@router.get("")
def list_documents(session: Session = Depends(db_session)) -> dict[str, Any]:
    documents = list(session.scalars(select(Document).order_by(Document.created_at.desc())))
    counts = _counts(session, [document.id for document in documents])
    return {
        "documents": [
            document_summary(document, counts.get(document.id, {})) for document in documents
        ]
    }


@router.post("", status_code=202)
async def upload_document(
    background: BackgroundTasks,
    file: UploadFile,
    session: Session = Depends(db_session),
) -> dict[str, Any]:
    settings = get_settings()
    settings.ensure_directories()

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="the uploaded file is empty")
    if len(payload) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"file exceeds the {settings.max_upload_mb} MB upload limit",
        )
    if not payload.startswith(b"%PDF"):
        raise HTTPException(status_code=415, detail="only PDF files are accepted")

    digest = sha256_bytes(payload)
    existing = session.scalar(select(Document).where(Document.sha256 == digest))
    if existing is not None:
        # Content-addressed, so re-uploading the same bytes is recognised whatever the file
        # was renamed to. Nothing is re-processed unless the caller asks for it.
        return {
            "document": document_summary(
                existing, _counts(session, [existing.id]).get(existing.id, {})
            ),
            "job": None,
            "duplicate": True,
            "message": "this document is already in the knowledge layer",
        }

    safe_name = Path(file.filename or "document.pdf").name
    stored = settings.upload_dir / f"{digest[:16]}-{safe_name}"
    stored.write_bytes(payload)

    document = Document(
        sha256=digest,
        filename=safe_name,
        stored_path=str(stored),
        byte_size=len(payload),
        status=DOC_STATUS_PENDING,
    )
    session.add(document)
    session.flush()

    job = Job(document_id=document.id, stage="queued", message="waiting to start")
    session.add(job)
    session.commit()

    background.add_task(_run_ingest, document.id, job.id)
    return {
        "document": document_summary(document, {}),
        "job": job_summary(job),
        "duplicate": False,
    }


@router.post("/{document_id}/reprocess", status_code=202)
def reprocess_document(
    document_id: int,
    background: BackgroundTasks,
    session: Session = Depends(db_session),
) -> dict[str, Any]:
    """Re-run the pipeline over a document already in the layer.

    Useful after a prompt or rule change. Cached model responses mean an unchanged document
    costs almost nothing to reprocess, so this is the normal way to adopt an improvement
    without re-reading the corpus.
    """
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    if not Path(document.stored_path).is_file():
        raise HTTPException(status_code=410, detail="the stored file for this document is missing")

    job = Job(document_id=document.id, stage="queued", message="waiting to start")
    session.add(job)
    session.commit()

    background.add_task(_run_ingest, document.id, job.id)
    return {"job": job_summary(job)}


@router.get("/{document_id}")
def get_document(document_id: int, session: Session = Depends(db_session)) -> dict[str, Any]:
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    pages = list(
        session.scalars(
            select(Page).where(Page.document_id == document_id).order_by(Page.page_number)
        )
    )
    counts = _counts(session, [document_id]).get(document_id, {})
    return document_detail(document, counts, pages)


@router.delete("/{document_id}", status_code=204)
def delete_document(document_id: int, session: Session = Depends(db_session)) -> Response:
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")

    # Relations reference facts in two documents, so the ones crossing into this document
    # have to go explicitly; the cascade only covers rows owned by it.
    fact_ids = select(Fact.id).where(Fact.document_id == document_id)
    session.execute(
        delete(Relation).where(
            Relation.left_fact_id.in_(fact_ids) | Relation.right_fact_id.in_(fact_ids)
        )
    )
    stored = Path(document.stored_path)
    session.delete(document)
    session.commit()

    if stored.is_file():
        try:
            stored.unlink()
        except OSError as error:
            logger.warning("could not remove stored file %s: %s", stored, error)
    return Response(status_code=204)


@router.get("/{document_id}/file")
def get_document_file(document_id: int, session: Session = Depends(db_session)) -> FileResponse:
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    path = Path(document.stored_path)
    if not path.is_file():
        raise HTTPException(status_code=410, detail="the stored file is no longer available")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=document.filename,
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/{document_id}/pages/{page_number}/image")
def get_page_image(
    document_id: int,
    page_number: int,
    dpi: int = Query(default=130, ge=72, le=300),
    session: Session = Depends(db_session),
) -> Response:
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    path = Path(document.stored_path)
    if not path.is_file():
        raise HTTPException(status_code=410, detail="the stored file is no longer available")
    if page_number < 1 or page_number > max(document.page_count, 1):
        raise HTTPException(status_code=404, detail="page not found")

    try:
        image = render_page_png(path, page_number, dpi=dpi)
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"could not render page: {error}") from error
    return Response(
        content=image,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/{document_id}/pages/{page_number}/text")
def get_page_text(
    document_id: int, page_number: int, session: Session = Depends(db_session)
) -> dict[str, Any]:
    page = session.scalar(
        select(Page).where(Page.document_id == document_id, Page.page_number == page_number)
    )
    if page is None:
        raise HTTPException(status_code=404, detail="page not found")
    return {
        "page_number": page.page_number,
        "printed_label": page.printed_label,
        "page_type": page.page_type,
        "text": page.text,
        "rendition": page.rendition,
        "width": page.width,
        "height": page.height,
        "layout": page.layout or {},
    }


def _run_ingest(document_id: int, job_id: int) -> None:
    """Entry point for the background task.

    FastAPI runs background tasks in a worker thread, which has no running event loop, so
    the pipeline's own loop is created here rather than assuming one exists.
    """
    try:
        asyncio.run(ingest_document(document_id, job_id))
    except Exception:
        logger.exception("ingest task failed for document %s", document_id)
