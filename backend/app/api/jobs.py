"""Ingest job status and progress streaming."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.serializers import job_summary
from app.db.engine import db_session, session_scope
from app.db.models import Job

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

POLL_INTERVAL_SECONDS = 0.6
HEARTBEAT_SECONDS = 15.0
TERMINAL_STAGES = {"done", "failed"}


@router.get("")
def list_jobs(
    session: Session = Depends(db_session),
    document_id: int | None = None,
    active_only: bool = False,
    limit: int = 25,
) -> dict[str, Any]:
    statement = select(Job).order_by(Job.started_at.desc()).limit(limit)
    if document_id is not None:
        statement = statement.where(Job.document_id == document_id)
    if active_only:
        statement = statement.where(Job.stage.notin_(TERMINAL_STAGES))
    return {"jobs": [job_summary(job) for job in session.scalars(statement)]}


@router.get("/{job_id}")
def get_job(job_id: int, session: Session = Depends(db_session)) -> dict[str, Any]:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job_summary(job)


@router.get("/{job_id}/stream")
async def stream_job(job_id: int, request: Request) -> StreamingResponse:
    """Server-sent events for one ingest job.

    Polling the job row is enough here and avoids a message broker for what is a single
    writer and a handful of readers. The pipeline writes progress into the row and WAL mode
    lets this read it while the ingest transaction is still open.
    """
    with session_scope() as session:
        if session.get(Job, job_id) is None:
            raise HTTPException(status_code=404, detail="job not found")

    async def events() -> AsyncIterator[str]:
        last_payload: str | None = None
        idle = 0.0
        while True:
            if await request.is_disconnected():
                return

            with session_scope() as session:
                job = session.get(Job, job_id)
                payload = json.dumps(job_summary(job)) if job else None

            if payload is None:
                yield 'event: error\ndata: {"detail":"job disappeared"}\n\n'
                return

            if payload != last_payload:
                last_payload = payload
                idle = 0.0
                yield f"data: {payload}\n\n"
            else:
                idle += POLL_INTERVAL_SECONDS
                if idle >= HEARTBEAT_SECONDS:
                    idle = 0.0
                    # Keeps intermediaries from closing an idle connection during a long
                    # model call, when the job legitimately has nothing new to report.
                    yield ": keep-alive\n\n"

            if json.loads(payload)["stage"] in TERMINAL_STAGES:
                return

            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
