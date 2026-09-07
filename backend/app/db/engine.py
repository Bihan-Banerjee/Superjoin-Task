"""Engine, session handling and the bits of schema SQLAlchemy will not express.

SQLite is the right store here: the whole knowledge layer is one file that can be copied,
committed as an evaluation snapshot, and opened with no server. The cost is that
concurrent writers need care, which is what the pragmas below are for.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import Base

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _apply_pragmas(dbapi_connection: sqlite3.Connection, _record: object) -> None:
    cursor = dbapi_connection.cursor()
    # WAL lets the API keep reading while an ingest writes, which is the whole point of
    # streaming progress to the UI during a long job.
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=15000")
    cursor.execute("PRAGMA temp_store=MEMORY")
    cursor.execute("PRAGMA cache_size=-65536")
    cursor.close()


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        settings.ensure_directories()
        _engine = create_engine_for(settings.sqlalchemy_url)
    return _engine


def create_engine_for(url: str) -> Engine:
    from sqlalchemy import create_engine

    engine = create_engine(
        url,
        future=True,
        pool_pre_ping=True,
        connect_args={"check_same_thread": False, "timeout": 15},
    )
    event.listen(engine, "connect", _apply_pragmas)
    return engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def db_session() -> Iterator[Session]:
    """FastAPI dependency."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


# Full-text index over the searchable surfaces of a fact. Kept as an external-content-free
# table with explicit triggers: contentless FTS5 cannot be re-read for snippets, and the
# UI wants to highlight matches inside the stored quote.
_FTS_SETUP = (
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
        statement,
        subject_surface,
        predicate_surface,
        evidence_quote,
        period_label,
        content='facts',
        content_rowid='id',
        tokenize='unicode61 remove_diacritics 2'
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS facts_fts_insert AFTER INSERT ON facts BEGIN
        INSERT INTO facts_fts(rowid, statement, subject_surface, predicate_surface,
                              evidence_quote, period_label)
        VALUES (new.id, new.statement, new.subject_surface, new.predicate_surface,
                new.evidence_quote, new.period_label);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS facts_fts_delete AFTER DELETE ON facts BEGIN
        INSERT INTO facts_fts(facts_fts, rowid, statement, subject_surface, predicate_surface,
                              evidence_quote, period_label)
        VALUES ('delete', old.id, old.statement, old.subject_surface, old.predicate_surface,
                old.evidence_quote, old.period_label);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS facts_fts_update AFTER UPDATE ON facts BEGIN
        INSERT INTO facts_fts(facts_fts, rowid, statement, subject_surface, predicate_surface,
                              evidence_quote, period_label)
        VALUES ('delete', old.id, old.statement, old.subject_surface, old.predicate_surface,
                old.evidence_quote, old.period_label);
        INSERT INTO facts_fts(rowid, statement, subject_surface, predicate_surface,
                              evidence_quote, period_label)
        VALUES (new.id, new.statement, new.subject_surface, new.predicate_surface,
                new.evidence_quote, new.period_label);
    END
    """,
)


def init_database(engine: Engine | None = None) -> None:
    engine = engine or get_engine()
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        for statement in _FTS_SETUP:
            connection.execute(text(statement))


def rebuild_fts(engine: Engine | None = None) -> None:
    engine = engine or get_engine()
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO facts_fts(facts_fts) VALUES ('rebuild')"))


def reset_engine() -> None:
    """Drop cached engine and session factory. Used by tests and by snapshot loading."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
