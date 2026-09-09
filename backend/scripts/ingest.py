"""Ingest PDFs from the command line.

The API is the intended entry point, but a script is better for the first run over a corpus:
it fails loudly, prints per-document statistics, and can be re-run safely because ingest is
content-addressed and model responses are cached.

    python scripts/ingest.py ../samples/delhivery/03-delhivery-q4-fy24-earnings-presentation.pdf
    python scripts/ingest.py ../samples --recursive
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core.hashing import sha256_file  # noqa: E402
from app.db.engine import init_database, session_scope  # noqa: E402
from app.db.models import DOC_STATUS_PENDING, Document, Job  # noqa: E402
from app.main import configure_logging, use_utf8_console  # noqa: E402
from app.pipeline.orchestrator import ingest_document  # noqa: E402

logger = logging.getLogger("ingest")


def collect(paths: list[str], recursive: bool) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            pattern = "**/*.pdf" if recursive else "*.pdf"
            files.extend(sorted(path.glob(pattern)))
        elif path.is_file() and path.suffix.lower() == ".pdf":
            files.append(path)
        else:
            logger.warning("skipping %s: not a PDF", path)
    return files


def register(path: Path, *, force: bool) -> tuple[int, int] | None:
    """Create or reuse the document row, and open a job for it.

    Returns None when the file is already ingested and `force` was not given, which is what
    makes re-running the script over a whole directory cheap.
    """
    settings = get_settings()
    settings.ensure_directories()
    digest = sha256_file(path)

    with session_scope() as session:
        document = session.scalar(select(Document).where(Document.sha256 == digest))
        if document is not None and not force:
            logger.info("%s is already ingested, skipping", path.name)
            return None

        if document is None:
            stored = settings.upload_dir / f"{digest[:16]}-{path.name}"
            if not stored.exists():
                stored.write_bytes(path.read_bytes())
            document = Document(
                sha256=digest,
                filename=path.name,
                stored_path=str(stored),
                byte_size=path.stat().st_size,
                status=DOC_STATUS_PENDING,
            )
            session.add(document)
            session.flush()

        job = Job(document_id=document.id, stage="queued", message="started from the command line")
        session.add(job)
        session.flush()
        return document.id, job.id


async def run(files: list[Path], *, force: bool) -> int:
    settings = get_settings()
    for warning in settings.throttle_warnings():
        logger.warning(warning)

    failures = 0
    for index, path in enumerate(files, start=1):
        registered = register(path, force=force)
        if registered is None:
            continue

        document_id, job_id = registered
        logger.info("[%d/%d] ingesting %s", index, len(files), path.name)
        started = time.perf_counter()
        try:
            stats = await ingest_document(document_id, job_id, settings)
        except Exception as error:
            failures += 1
            logger.error("[%d/%d] %s failed: %s", index, len(files), path.name, error)
            continue

        elapsed = time.perf_counter() - started
        summary = stats.as_dict()
        logger.info(
            "[%d/%d] %s in %.0fs: %d facts kept of %d proposed (%.0f%% grounded), "
            "%d relations, %d rejections",
            index,
            len(files),
            path.name,
            elapsed,
            summary["facts_kept"],
            summary["facts_proposed"],
            summary["grounding_pass_rate"] * 100,
            summary["relations_created"],
            summary["rejections"],
        )
        usage = summary.get("usage", {})
        if usage:
            logger.info(
                "         %d model calls (%d billed, %d cached), %d in / %d out tokens",
                usage.get("calls", 0),
                usage.get("billed_calls", 0),
                usage.get("cache_hits", 0),
                usage.get("input_tokens", 0),
                usage.get("output_tokens", 0),
            )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="PDF files or directories containing them")
    parser.add_argument(
        "--recursive", action="store_true", help="descend into subdirectories of a given path"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-process a document already in the layer instead of skipping it",
    )
    parser.add_argument("--log-level", default="INFO")
    arguments = parser.parse_args()

    configure_logging(arguments.log_level)
    use_utf8_console()
    init_database()

    files = collect(arguments.paths, arguments.recursive)
    if not files:
        logger.error("no PDFs found in %s", ", ".join(arguments.paths))
        return 1

    logger.info("ingesting %d document(s)", len(files))
    started = time.perf_counter()
    failures = asyncio.run(run(files, force=arguments.force))
    logger.info("finished in %.0fs with %d failure(s)", time.perf_counter() - started, failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
