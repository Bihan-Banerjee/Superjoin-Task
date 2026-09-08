"""Bulk export, and the read-only guard.

Both are about what a reviewer can do with the layer without a key: take the data away, and
be trusted not to change anything.
"""

from __future__ import annotations

import csv
import io
import json

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings, reset_settings_cache
from app.db import engine as engine_module
from app.db.engine import init_database, session_scope
from app.db.models import Document, Fact, Page, Relation


@pytest.fixture
def layer(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv(
        "DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'export.sqlite').as_posix()}"
    )
    reset_settings_cache()
    get_settings().ensure_directories()
    engine_module.reset_engine()
    init_database(engine_module.get_engine())

    with session_scope() as session:
        document = Document(
            sha256="a" * 64,
            filename="filing.pdf",
            stored_path="filing.pdf",
            title="Annual Report FY24",
            publisher="Delhivery Limited",
        )
        session.add(document)
        session.flush()
        page = Page(document_id=document.id, page_number=7, text="x", text_sha="b" * 64)
        session.add(page)
        session.flush()

        facts = [
            Fact(
                document_id=document.id,
                page_id=page.id,
                statement=f"statement {index}",
                subject_surface="Delhivery Limited",
                predicate_surface="revenue from services",
                value_text=f"{index},000 Cr",
                value_number=float(index * 1000),
                value_base=float(index * 1000) * 1e7,
                unit_canonical="INR crore",
                unit_class="currency",
                currency="INR",
                period_label="FY24",
                # Commas and quotes here are the point: they must survive the CSV.
                qualifiers={"segment": 'Express, "Parcel"'},
                evidence_quote=f'he said "{index},000 Cr", plainly',
                confidence=0.9,
            )
            for index in (1, 2)
        ]
        session.add_all(facts)
        session.flush()
        session.add(
            Relation(
                left_fact_id=facts[0].id,
                right_fact_id=facts[1].id,
                relation_type="contradicts",
                dimension="none",
                decided_by="rule",
                rule_id="values-disagree",
                confidence=0.82,
                severity=0.5,
                delta_relative=0.5,
                explanation="the values differ",
                raw={"order_sensitive": True},
            )
        )
    yield
    engine_module.reset_engine()
    reset_settings_cache()


def client() -> TestClient:
    from app.main import app

    return TestClient(app)


def test_facts_export_is_valid_csv_with_a_header(layer):
    with client() as api:
        response = api.get("/api/export/facts")
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "attachment" in response.headers["content-disposition"]

    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 2
    assert rows[0]["document"] == "Annual Report FY24"
    assert rows[0]["measure"] == "revenue from services"


def test_commas_and_quotes_in_a_quote_do_not_break_the_csv(layer):
    """Evidence is verbatim text, so it contains whatever the document contained."""
    with client() as api:
        response = api.get("/api/export/facts")

    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert rows[0]["evidence_quote"] == 'he said "1,000 Cr", plainly'
    assert rows[0]["qualifiers"] == 'segment=Express, "Parcel"'


def test_json_export_keeps_the_full_shape(layer):
    with client() as api:
        response = api.get("/api/export/facts", params={"format": "json"})
    payload = json.loads(response.text)
    assert len(payload) == 2
    assert payload[0]["value_base"] == 1000 * 1e7


def test_relations_export_carries_both_sides(layer):
    with client() as api:
        response = api.get("/api/export/relations")
    rows = list(csv.DictReader(io.StringIO(response.text)))

    assert len(rows) == 1
    assert rows[0]["type"] == "contradicts"
    assert rows[0]["left_value"] == "1,000 Cr"
    assert rows[0]["right_value"] == "2,000 Cr"
    assert rows[0]["order_sensitive"] == "True"


def test_export_can_be_limited_to_one_document(layer):
    with client() as api:
        assert (
            len(
                list(
                    csv.DictReader(
                        io.StringIO(api.get("/api/export/facts", params={"document_id": 1}).text)
                    )
                )
            )
            == 2
        )
        assert (
            len(
                list(
                    csv.DictReader(
                        io.StringIO(api.get("/api/export/facts", params={"document_id": 999}).text)
                    )
                )
            )
            == 0
        )


def test_read_only_refuses_to_change_the_layer(layer, monkeypatch):
    monkeypatch.setenv("READ_ONLY", "true")
    reset_settings_cache()

    with client() as api:
        deleted = api.delete("/api/documents/1")
        reprocessed = api.post("/api/documents/1/reprocess")
        uploaded = api.post(
            "/api/documents", files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")}
        )

    for response in (deleted, reprocessed, uploaded):
        assert response.status_code == 403
        assert "read-only" in response.json()["detail"]


def test_read_only_still_serves_everything_worth_reading(layer, monkeypatch):
    monkeypatch.setenv("READ_ONLY", "true")
    reset_settings_cache()

    with client() as api:
        assert api.get("/api/documents").status_code == 200
        assert api.get("/api/facts").status_code == 200
        assert api.get("/api/export/facts").status_code == 200
        assert api.get("/api/health").json()["read_only"] is True


def test_writing_is_allowed_when_read_only_is_off(layer, monkeypatch):
    monkeypatch.setenv("READ_ONLY", "false")
    reset_settings_cache()

    with client() as api:
        # 404 rather than 403: the guard is out of the way and the handler ran.
        assert api.post("/api/documents/999/reprocess").status_code == 404


def test_the_snapshot_refuses_responses_a_test_wrote(tmp_path):
    """The replay cache is always consulted, so a stub's answer in it shadows the stub.

    An earlier export copied the runtime cache wholesale, including two responses invented by
    a provider in the test suite. Those entries then served the very tests that created them:
    the stub was never called, its call-count assertions failed, and the verdict came back
    from disk. A recording is only worth shipping if it captures what a real provider said.
    """
    import json
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from snapshot import _copy_responses

    source = tmp_path / "cache"
    (source / "ab").mkdir(parents=True)
    (source / "ab" / "real.json").write_text(
        json.dumps({"provider": "gemini", "data": {}}), encoding="utf-8"
    )
    (source / "ab" / "stub.json").write_text(
        json.dumps({"provider": "order-sensitive", "data": {}}), encoding="utf-8"
    )
    (source / "ab" / "broken.json").write_text("not json", encoding="utf-8")

    destination = tmp_path / "replay"
    assert _copy_responses(source, destination) == 1
    copied = {path.name for path in destination.rglob("*.json")}
    assert copied == {"real.json"}
