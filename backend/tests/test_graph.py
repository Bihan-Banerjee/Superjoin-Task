"""The optional graph projection.

Two things worth pinning down: that it stays off unless someone asks for it, and that what
it returns is the relation table and not a second opinion about it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings, reset_settings_cache
from app.db import engine as engine_module
from app.db.engine import init_database, session_scope
from app.db.models import Document, Fact, Page, Relation


@pytest.fixture
def layer(tmp_path, monkeypatch):
    """A small corpus: two documents, five facts, four relations, one fact isolated."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv(
        "DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'graph.sqlite').as_posix()}"
    )
    reset_settings_cache()
    get_settings().ensure_directories()
    engine_module.reset_engine()
    init_database(engine_module.get_engine())

    with session_scope() as session:
        pages = {}
        for index in (1, 2):
            document = Document(
                sha256=str(index) * 64, filename=f"doc{index}.pdf", stored_path=f"doc{index}.pdf"
            )
            session.add(document)
            session.flush()
            page = Page(document_id=document.id, page_number=1, text="x", text_sha=str(index) * 64)
            session.add(page)
            session.flush()
            pages[index] = (document.id, page.id)

        facts = []
        for index in range(5):
            document_id, page_id = pages[1 if index < 3 else 2]
            fact = Fact(
                document_id=document_id,
                page_id=page_id,
                statement=f"fact {index}",
                subject_surface="India",
                predicate_surface="real gdp growth",
                value_text=f"{index}%",
                evidence_quote=f"fact {index}",
            )
            session.add(fact)
            facts.append(fact)
        session.flush()
        ids = [fact.id for fact in facts]

        # Facts 0-3 are connected; fact 4 is deliberately left with no relation.
        for left, right, severity, kind in (
            (0, 3, 0.9, "contradicts"),
            (1, 3, 0.5, "corroborates"),
            (2, 3, 0.2, "reconciled_by_context"),
            (0, 1, 0.1, "corroborates"),
        ):
            session.add(
                Relation(
                    left_fact_id=ids[left],
                    right_fact_id=ids[right],
                    relation_type=kind,
                    severity=severity,
                    confidence=0.8,
                    cross_document=True,
                    raw={},
                )
            )

    yield ids
    engine_module.reset_engine()
    reset_settings_cache()


def call(**params):
    """Through the real router, so route ordering is covered too.

    `/relations/graph` has to resolve ahead of `/relations/{relation_id}`, and calling the
    function directly would never notice if it stopped doing so.
    """
    from app.main import app

    with TestClient(app) as client:
        return client.get("/api/relations/graph", params=params)


def test_the_graph_is_refused_unless_it_is_switched_on(layer, monkeypatch):
    monkeypatch.setenv("ENABLE_GRAPH_VIEW", "false")
    reset_settings_cache()

    response = call()
    assert response.status_code == 404
    assert "ENABLE_GRAPH_VIEW" in response.json()["detail"]


def test_graph_resolves_ahead_of_the_relation_id_route(layer, monkeypatch):
    """Otherwise "graph" is parsed as an id and the endpoint 422s instead of answering."""
    monkeypatch.setenv("ENABLE_GRAPH_VIEW", "true")
    reset_settings_cache()

    assert call().status_code == 200


def test_switching_it_on_projects_the_relation_rows(layer, monkeypatch):
    monkeypatch.setenv("ENABLE_GRAPH_VIEW", "true")
    reset_settings_cache()

    graph = call().json()
    assert len(graph["edges"]) == 4
    assert graph["total_relations"] == 4
    assert graph["truncated"] is False


def test_a_fact_no_relation_touches_is_not_a_node(layer, monkeypatch):
    """Isolated facts are the overwhelming majority and would bury everything else."""
    monkeypatch.setenv("ENABLE_GRAPH_VIEW", "true")
    reset_settings_cache()

    graph = call().json()
    assert layer[4] not in {node["id"] for node in graph["nodes"]}
    assert len(graph["nodes"]) == 4


def test_the_cap_keeps_the_disagreements(layer, monkeypatch):
    """Truncation has to drop the least interesting edges, not an arbitrary slice."""
    monkeypatch.setenv("ENABLE_GRAPH_VIEW", "true")
    reset_settings_cache()

    graph = call(limit=10).json()
    kept = [edge["type"] for edge in graph["edges"][:1]]
    assert kept == ["contradicts"]


def test_filters_reach_the_graph_the_same_way_they_reach_the_table(layer, monkeypatch):
    monkeypatch.setenv("ENABLE_GRAPH_VIEW", "true")
    reset_settings_cache()

    graph = call(relation_type="corroborates").json()
    assert {edge["type"] for edge in graph["edges"]} == {"corroborates"}
    # Still reports how many matched the filter, so the UI can say what was left out.
    assert graph["total_relations"] == 2


def test_the_warning_is_only_raised_when_the_view_is_on(monkeypatch):
    monkeypatch.setenv("ENABLE_GRAPH_VIEW", "false")
    reset_settings_cache()
    assert get_settings().graph_view_warning() is None

    monkeypatch.setenv("ENABLE_GRAPH_VIEW", "true")
    reset_settings_cache()
    warning = get_settings().graph_view_warning()
    assert warning is not None and "not the reasoning" in warning
    reset_settings_cache()
