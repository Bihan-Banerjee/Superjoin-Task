"""Documents with no text layer.

The failure mode this covers is the quiet one. A scanned PDF parses without error, produces
no words, classifies every page as empty and finishes "successfully" with nothing to show —
which looks exactly like a bug in extraction rather than a property of the file.

The tests build a real scanned page: a rendered raster wrapped in a fresh PDF, with the text
layer genuinely absent rather than stubbed out.
"""

from __future__ import annotations

import shutil

import pymupdf
import pytest

from app.db.models import PAGE_SCANNED
from app.pipeline.classify import classify_page
from app.pipeline.parse import parse_pdf
from tests.factories import FILING_PAGES, build_pdf

TESSERACT = shutil.which("tesseract")


@pytest.fixture(scope="module")
def scanned_pdf(tmp_path_factory):
    """A page rendered to an image and wrapped in a new PDF — a print-to-scan, in effect."""
    directory = tmp_path_factory.mktemp("scanned")
    source = build_pdf(directory / "source.pdf", FILING_PAGES)

    original = pymupdf.open(source)
    scanned = pymupdf.open()
    for page in original:
        pixmap = page.get_pixmap(dpi=200)
        target = scanned.new_page(width=page.rect.width, height=page.rect.height)
        target.insert_image(target.rect, pixmap=pixmap)
    path = directory / "scanned.pdf"
    scanned.save(path)
    scanned.close()
    original.close()
    return path


def test_the_fixture_really_has_no_text_layer(scanned_pdf):
    """Guards the test itself: if this fails, everything below is proving nothing."""
    document = pymupdf.open(scanned_pdf)
    try:
        assert all(not page.get_text("text").strip() for page in document)
    finally:
        document.close()


def test_a_scanned_page_is_reported_as_scanned_not_empty(scanned_pdf):
    parsed = parse_pdf(scanned_pdf)
    signals = [classify_page(page) for page in parsed.pages]

    assert all(signal.page_type == PAGE_SCANNED for signal in signals)
    assert "no text layer" in signals[0].reason


def test_the_document_says_it_needs_ocr(scanned_pdf):
    """A caller should be able to tell without inspecting every page."""
    assert parse_pdf(scanned_pdf).needs_ocr is True


def test_a_normal_document_is_not_mistaken_for_a_scan(tmp_path):
    parsed = parse_pdf(build_pdf(tmp_path / "normal.pdf", FILING_PAGES))
    assert parsed.needs_ocr is False
    assert not any(page.looks_scanned for page in parsed.pages)


def test_asking_for_ocr_without_tesseract_degrades_rather_than_fails(scanned_pdf, monkeypatch):
    """The document must still ingest, with its scanned pages reported as unread."""

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("tesseract is not installed")

    monkeypatch.setattr(pymupdf.Page, "get_textpage_ocr", unavailable, raising=False)
    parsed = parse_pdf(scanned_pdf, ocr=True)

    assert parsed.pages, "pages must survive a failed OCR pass"
    assert not any(page.ocr_applied for page in parsed.pages)


@pytest.mark.skipif(TESSERACT is None, reason="Tesseract is not installed on this machine")
def test_ocr_recovers_text_a_scan_had_thrown_away(scanned_pdf):
    parsed = parse_pdf(scanned_pdf, ocr=True, ocr_dpi=200)
    recovered = [page for page in parsed.pages if page.ocr_applied]

    assert recovered, "OCR should have read at least one page"
    first = recovered[0]
    assert first.words, "OCR text must come with word boxes, or nothing can be highlighted"
    # Every downstream stage works off page text, so the recovered text has to be real text.
    assert len(first.text.strip()) > 100
    assert not first.looks_scanned, "a page OCR has read is no longer unreadable"
