"""ADR-0005 §2 — heuristic clause index. Pure."""

from __future__ import annotations

from app.documents.clauses import coverage, index_clauses
from app.documents.pdf import PageText

SAMPLE = """HOVEDKONTRAKT
8. Servicemål
8.1 Oppetid
Leverandøren garanterer en oppetid på minimum 99,8 %.
8.2 Service credits
Ved oppetid under 99,8 % ifalder leverandøren service credits, jf. bilag 5.
14.3 Forlængelse
Kunden kan forlænge kontrakten med op til 2 × 12 måneder.
30-09-2026 er sidste frist.
612.500,00 kr. pr. måned
Bilag 5 – Service credits
Tabel 1: Satser
§ 3 Definitioner
"""


def test_finds_numbered_bilag_tabel_and_paragraph_headings():
    clauses = index_clauses([PageText(page_pdf=12, text=SAMPLE, page_printed="12")])
    refs = [c.clause_ref for c in clauses]
    assert refs == ["8", "8.1", "8.2", "14.3", "bilag 5", "tabel 1", "§ 3"]
    by_ref = {c.clause_ref: c for c in clauses}
    assert by_ref["8.2"].heading == "Service credits"
    assert by_ref["bilag 5"].heading == "Service credits"
    assert by_ref["tabel 1"].heading == "Satser"
    assert all(c.page_pdf == 12 for c in clauses)


def test_dates_and_amounts_are_not_headings():
    text = "30-09-2026 er sidste frist.\n612.500,00 kr. pr. måned\n1.184,00 kr\n"
    assert index_clauses([PageText(page_pdf=1, text=text, page_printed=None)]) == []


def test_offsets_point_at_the_heading_line():
    page = PageText(page_pdf=1, text=SAMPLE, page_printed=None)
    c = next(c for c in index_clauses([page]) if c.clause_ref == "8.2")
    assert SAMPLE[c.char_start : c.char_end] == "8.2 Service credits"


def test_zero_matches_is_a_valid_outcome():
    pages = [PageText(page_pdf=1, text="Bare løbende tekst uden numre.", page_printed=None)]
    assert index_clauses(pages) == []
    assert coverage(pages, []) == 0.0


def test_coverage_is_share_of_pages_with_a_clause():
    pages = [
        PageText(page_pdf=1, text="1. Indledning\n", page_printed=None),
        PageText(page_pdf=2, text="fritekst", page_printed=None),
    ]
    assert coverage(pages, index_clauses(pages)) == 0.5
