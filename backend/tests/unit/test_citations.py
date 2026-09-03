"""ADR-0005 §3 — locate the quote, do not trust the page. Pure."""

from __future__ import annotations

from app.ai.citations import Clause, Page, cap, label, locate
from app.domain.models import Confidence

PAGES = [
    Page(1, "1", "1. Indledning\nAftalen træder i kraft den 1. januar 2027."),
    Page(
        2,
        "2",
        "8. Servicemål\n8.1 Oppetid\nLeverandøren garanterer en   oppetid på minimum 99,8 %.\n8.2 Service credits\nVed oppetid under 99,8 % ifalder leverandøren service credits.",
    ),
]
CLAUSES = [Clause("1", 1, 0), Clause("8", 2, 0), Clause("8.1", 2, 14), Clause("8.2", 2, 90)]


def test_exact_match_on_claimed_page_gives_clause():
    loc = locate(PAGES, CLAUSES, "garanterer en oppetid på minimum 99,8 %", 2)
    assert loc.verified and loc.page_pdf == 2 and loc.clause_ref == "8.1"


def test_wrong_claimed_page_is_corrected():
    loc = locate(PAGES, CLAUSES, "træder i kraft den 1. januar 2027", 2)
    assert loc.verified and loc.page_pdf == 1 and loc.page_printed == "1" and loc.clause_ref == "1"


def test_whitespace_and_case_are_tolerated():
    loc = locate(PAGES, CLAUSES, "LEVERANDØREN GARANTERER EN OPPETID", 2)
    assert loc.verified


def test_paraphrase_falls_back_to_word_overlap():
    loc = locate(PAGES, CLAUSES, "leverandøren ifalder service credits ved oppetid under 99,8", 1)
    assert loc.verified and loc.page_pdf == 2


def test_not_found_is_unverified_and_caps_confidence():
    loc = locate(PAGES, CLAUSES, "Bod på 5 % af kontraktsummen pr. påbegyndt uge", 2)
    assert not loc.verified
    assert cap(Confidence.hoej, all_verified=False) == Confidence.lav
    assert cap(Confidence.hoej, all_verified=True) == Confidence.hoej


def test_label_is_derived():
    assert label("Hovedkontrakt", 12, "12", "8.2") == "Hovedkontrakt · s. 12 · pkt. 8.2"
    assert label("Bilag 5", 2, None, "tabel 1") == "Bilag 5 · s. 2 · tabel 1"
    assert label("Rammeaftale", None, None, None) == "Rammeaftale"
