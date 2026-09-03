"""ADR-0005 §1 — page text extraction with printed page numbers. Uses pymupdf to
build the fixture, so no binary test assets live in the repo."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from app.documents.pdf import extract_pages, looks_scanned


def _pdf(path: Path, pages: list[tuple[str, str | None]]) -> Path:
    doc = pymupdf.open()
    for body, footer in pages:
        page = doc.new_page()
        page.insert_text((72, 72), body, fontsize=11)
        if footer:
            page.insert_text((250, 800), footer, fontsize=9)
    doc.save(str(path))
    doc.close()
    return path


def test_extracts_text_per_page_with_printed_numbers(tmp_path: Path):
    pdf = _pdf(
        tmp_path / "k.pdf",
        [("8.1 Oppetid\nMinimum 99,8 %.", "Side 1 af 2"), ("8.2 Service credits", "Side 2 af 2")],
    )
    pages = extract_pages(pdf)
    assert [p.page_pdf for p in pages] == [1, 2]
    assert "Oppetid" in pages[0].text
    assert "Service credits" in pages[1].text
    assert [p.page_printed for p in pages] == ["1", "2"]


def test_no_footer_means_no_printed_number(tmp_path: Path):
    pages = extract_pages(_pdf(tmp_path / "n.pdf", [("Kun brødtekst", None)]))
    assert pages[0].page_printed is None


def test_scanned_detection_on_empty_pages(tmp_path: Path):
    blank = extract_pages(_pdf(tmp_path / "b.pdf", [("", None), ("", None)]))
    assert looks_scanned(blank)
    text = extract_pages(_pdf(tmp_path / "t.pdf", [("Rigtig tekst på siden", None)]))
    assert not looks_scanned(text)
