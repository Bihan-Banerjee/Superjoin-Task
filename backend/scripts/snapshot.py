"""Export and restore an evaluation snapshot.

The assignment asks that a reviewer be able to judge the work without an account. Three
things are exported to make that possible:

* **The database file.** Opening the app against it shows the real knowledge layer with no
  credentials and no processing.
* **A JSON export.** Readable without running anything, and diffable, so a change in
  extraction quality between runs is visible in review rather than only in the interface.
* **The recorded model responses.** With `LLM_PROVIDER=replay` these re-run the exact
  pipeline offline, so the extraction path itself can be exercised rather than taken on
  trust. A request with no recording fails loudly instead of being invented.

    python scripts/snapshot.py export
    python scripts/snapshot.py load
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import BACKEND_ROOT, get_settings  # noqa: E402
from app.db.engine import init_database, reset_engine, session_scope  # noqa: E402
from app.db.models import (  # noqa: E402
    Document,
    Entity,
    Fact,
    Measure,
    Page,
    QualifierKey,
    Rejection,
    Relation,
)
from app.main import configure_logging, use_utf8_console  # noqa: E402

logger = logging.getLogger("snapshot")

SEED_DIR = BACKEND_ROOT / "seed"
SNAPSHOT_DB = SEED_DIR / "knowledge.sqlite"
EXPORT_JSON = SEED_DIR / "export.json"
REPLAY_DIR = SEED_DIR / "replay"


def export_json() -> dict[str, Any]:
    """The knowledge layer as plain data, ordered so two runs diff cleanly."""
    with session_scope() as session:
        documents = list(session.scalars(select(Document).order_by(Document.id)))
        facts = list(session.scalars(select(Fact).order_by(Fact.id)))
        relations = list(session.scalars(select(Relation).order_by(Relation.id)))
        measures = list(session.scalars(select(Measure).order_by(Measure.id)))
        entities = list(session.scalars(select(Entity).order_by(Entity.id)))
        qualifiers = list(session.scalars(select(QualifierKey).order_by(QualifierKey.key)))
        rejections = list(session.scalars(select(Rejection).order_by(Rejection.id)))

        page_numbers = {
            page.id: (page.page_number, page.printed_label)
            for page in session.scalars(select(Page))
        }

        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "counts": {
                "documents": len(documents),
                "facts": len(facts),
                "relations": len(relations),
                "measures": len(measures),
                "entities": len(entities),
                "qualifier_keys": len(qualifiers),
                "rejections": len(rejections),
            },
            "documents": [
                {
                    "id": document.id,
                    "filename": document.filename,
                    "sha256": document.sha256,
                    "title": document.title,
                    "publisher": document.publisher,
                    "doc_type": document.doc_type,
                    "period_label": document.period_label,
                    "as_of_date": document.as_of_date,
                    "default_currency": document.default_currency,
                    "default_scale": document.default_scale,
                    "fiscal_convention": document.fiscal_convention,
                    "page_count": document.page_count,
                }
                for document in documents
            ],
            "measures": [
                {
                    "id": measure.id,
                    "name": measure.name,
                    "unit_class": measure.unit_class,
                    "aliases": list(measure.aliases or []),
                    "fact_count": measure.fact_count,
                    "document_count": measure.document_count,
                }
                for measure in measures
            ],
            "entities": [
                {
                    "id": entity.id,
                    "name": entity.name,
                    "aliases": list(entity.aliases or []),
                    "fact_count": entity.fact_count,
                }
                for entity in entities
            ],
            "qualifier_keys": [
                {"key": key.key, "values": list(key.values or []), "fact_count": key.fact_count}
                for key in qualifiers
            ],
            "facts": [
                {
                    "id": fact.id,
                    "document_id": fact.document_id,
                    "page": page_numbers.get(fact.page_id, (None, None))[0],
                    "printed_label": page_numbers.get(fact.page_id, (None, None))[1],
                    "kind": fact.kind,
                    "statement": fact.statement,
                    "subject": fact.subject_surface,
                    "predicate": fact.predicate_surface,
                    "measure_id": fact.measure_id,
                    "entity_id": fact.entity_id,
                    "value_text": fact.value_text,
                    "value_number": fact.value_number,
                    "value_base": fact.value_base,
                    "unit": fact.unit_canonical,
                    "unit_class": fact.unit_class,
                    "currency": fact.currency,
                    "period_label": fact.period_label,
                    "period_start": fact.period_start,
                    "period_end": fact.period_end,
                    "qualifiers": fact.qualifiers or {},
                    "basis": fact.basis,
                    "confidence": round(fact.confidence, 3),
                    "evidence_quote": fact.evidence_quote,
                    "evidence_exact": fact.evidence_exact,
                    "evidence_score": round(fact.evidence_score, 1),
                    "bboxes": fact.bboxes or [],
                }
                for fact in facts
            ],
            "relations": [
                {
                    "id": relation.id,
                    "left_fact_id": relation.left_fact_id,
                    "right_fact_id": relation.right_fact_id,
                    "type": relation.relation_type,
                    "subtype": relation.subtype,
                    "dimension": relation.dimension,
                    "explanation": relation.explanation,
                    "decided_by": relation.decided_by,
                    "rule_id": relation.rule_id,
                    "confidence": round(relation.confidence, 3),
                    "severity": round(relation.severity, 3),
                    "delta_relative": relation.delta_relative,
                    "cross_document": relation.cross_document,
                }
                for relation in relations
            ],
            "rejections": [
                {
                    "id": rejection.id,
                    "document_id": rejection.document_id,
                    "page": rejection.page_number,
                    "stage": rejection.stage,
                    "reason": rejection.reason,
                    "detail": rejection.detail,
                    "candidate": rejection.candidate or {},
                }
                for rejection in rejections
            ],
        }


def do_export(include_responses: bool) -> int:
    settings = get_settings()
    SEED_DIR.mkdir(parents=True, exist_ok=True)

    payload = export_json()
    EXPORT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("wrote %s (%s)", EXPORT_JSON.name, payload["counts"])

    # Checkpoint first: in WAL mode the committed data may still be sitting in the -wal
    # file, and copying the database alone would produce a snapshot missing the last run.
    reset_engine()
    source = settings.data_dir / "knowledge.sqlite"
    if source.is_file():
        import sqlite3

        with sqlite3.connect(source) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        shutil.copy2(source, SNAPSHOT_DB)
        logger.info("wrote %s (%.1f MB)", SNAPSHOT_DB.name, SNAPSHOT_DB.stat().st_size / 1e6)
    else:
        logger.warning("no database at %s, skipping the file snapshot", source)

    if include_responses:
        copied = _copy_responses(settings.cache_dir, REPLAY_DIR)
        logger.info("wrote %d recorded model responses to %s", copied, REPLAY_DIR.name)

    return 0


# Providers whose recorded answers are worth shipping. An allow-list rather than a deny-list,
# so a stub added to the test suite later is excluded by default instead of having to be
# remembered.
_REAL_PROVIDERS = frozenset({"gemini", "openrouter", "ollama"})


def _copy_responses(source: Path, destination: Path) -> int:
    """Copy recorded responses, skipping anything a test wrote.

    The runtime cache is shared by whatever ran against it, and the test suite used to write
    into it: so the first export shipped two answers invented by a stub provider in
    `test_adjudicate.py`. Because the replay cache is always consulted, those entries then
    shadowed the very tests that created them: the stub was never called, its assertions
    about call counts failed, and the verdict came back from disk.

    A recording is only useful for replay if it captures what a real provider said, so
    anything else is left behind.
    """
    if not source.is_dir():
        logger.warning("no response cache at %s", source)
        return 0
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)

    copied = 0
    skipped = 0
    for path in source.rglob("*.json"):
        try:
            provider = json.loads(path.read_text(encoding="utf-8")).get("provider", "")
        except (OSError, json.JSONDecodeError):
            skipped += 1
            continue
        if str(provider).strip().lower() not in _REAL_PROVIDERS:
            skipped += 1
            continue
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied += 1

    if skipped:
        logger.info("skipped %d cached response(s) not recorded from a real provider", skipped)
    return copied


def do_load(force: bool) -> int:
    settings = get_settings()
    settings.ensure_directories()

    if not SNAPSHOT_DB.is_file():
        logger.error("no snapshot at %s", SNAPSHOT_DB)
        return 1

    target = settings.data_dir / "knowledge.sqlite"
    if target.is_file() and not force:
        logger.error("%s already exists; pass --force to overwrite it", target)
        return 1

    reset_engine()
    for suffix in ("", "-wal", "-shm"):
        stale = Path(str(target) + suffix)
        if stale.exists():
            stale.unlink()

    shutil.copy2(SNAPSHOT_DB, target)
    init_database()
    logger.info("restored %s from the committed snapshot", target)

    with session_scope() as session:
        logger.info(
            "%d documents, %d facts, %d relations",
            session.query(Document).count(),
            session.query(Fact).count(),
            session.query(Relation).count(),
        )

    # Stored PDFs are referenced by absolute path, which will not match on another machine.
    _repoint_documents(settings.upload_dir)
    return 0


def _repoint_documents(upload_dir: Path) -> None:
    """Rewrite stored paths to this machine's upload directory.

    A snapshot taken elsewhere carries the paths of the machine that made it. Anything that
    can be matched by filename is repointed; the rest keep their recorded path and simply
    cannot render a page image, which the interface handles.
    """
    available = (
        {path.name: path for path in upload_dir.glob("*.pdf")} if upload_dir.is_dir() else {}
    )
    repointed = 0
    missing: list[str] = []

    with session_scope() as session:
        for document in session.scalars(select(Document)):
            current = Path(document.stored_path)
            if current.is_file():
                continue
            match = next(
                (path for name, path in available.items() if name.endswith(document.filename)),
                None,
            )
            if match is not None:
                document.stored_path = str(match)
                session.add(document)
                repointed += 1
            else:
                missing.append(document.filename)

    if repointed:
        logger.info("repointed %d document(s) at this machine's upload directory", repointed)
    if missing:
        logger.warning(
            "%d document file(s) are not present locally, so their page images cannot be "
            "rendered: %s",
            len(missing),
            ", ".join(missing[:5]),
        )
        logger.warning("copy the PDFs into %s to restore the evidence viewer", upload_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    export = subparsers.add_parser("export", help="write the committed snapshot")
    export.add_argument(
        "--no-responses",
        action="store_true",
        help="skip copying recorded model responses into the replay directory",
    )

    load = subparsers.add_parser("load", help="restore the committed snapshot")
    load.add_argument("--force", action="store_true", help="overwrite an existing database")

    parser.add_argument("--log-level", default="INFO")
    arguments = parser.parse_args()
    configure_logging(arguments.log_level)
    use_utf8_console()

    if arguments.command == "export":
        return do_export(include_responses=not arguments.no_responses)
    return do_load(force=arguments.force)


if __name__ == "__main__":
    raise SystemExit(main())
