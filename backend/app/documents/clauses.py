"""Heuristic clause index — ADR-0005 §2. Best-effort, no model.

Recognises numbered headings ("8.2 Service credits", "14.3."), "Bilag 5",
"Tabel 1" and "§ 3" at the start of a line. Zero matches is a valid outcome
(the document is then page-citable only); a false match costs nothing worse
than a wrong `pkt.` label on a chip, which the human sees next to the quote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.documents.pdf import PageText

# 1 / 1.2 / 1.2.3 (max 4 levels), optional trailing dot/paren, then a heading of
# at least three characters starting with a letter or quote.
_NUMBERED = re.compile(
    r"^(?P<ref>\d{1,2}(?:\.\d{1,2}){0,3})[.)]?\s+(?P<head>[A-ZÆØÅa-zæøå\"'(][^\n]{2,120})$"
)
_BILAG = re.compile(r"^(?P<ref>Bilag\s+\d{1,3})\b[\s:.\-–—]*(?P<head>[^\n]{0,120})$", re.IGNORECASE)
_TABEL = re.compile(r"^(?P<ref>Tabel\s+\d{1,3})\b[\s:.\-–—]*(?P<head>[^\n]{0,120})$", re.IGNORECASE)
_PARA = re.compile(r"^(?P<ref>§\s*\d{1,3}[a-z]?)\b[\s:.\-–—]*(?P<head>[^\n]{0,120})$")

# Lines that look numbered but are not headings: dates, amounts, page numbers.
_NOT_HEADING = re.compile(
    r"^\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|^\d+([.,]\d{3})*([.,]\d{2})?\s*(kr|%|dkk)", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class Clause:
    clause_ref: str
    heading: str
    page_pdf: int
    char_start: int  # offset within the page text
    char_end: int


def _normalise_ref(ref: str) -> str:
    r = re.sub(r"\s+", " ", ref.strip())
    if r.lower().startswith("bilag"):
        return "bilag " + r.split()[1]
    if r.lower().startswith("tabel"):
        return "tabel " + r.split()[1]
    if r.startswith("§"):
        return "§ " + r[1:].strip()
    return r.rstrip(".")


def index_clauses(pages: list[PageText]) -> list[Clause]:
    out: list[Clause] = []
    for page in pages:
        pos = 0
        for raw in page.text.splitlines(keepends=True):
            line = raw.rstrip("\r\n")
            start, end = pos, pos + len(line)
            pos += len(raw)
            stripped = line.strip()
            if not stripped or _NOT_HEADING.match(stripped):
                continue
            m = (
                _NUMBERED.match(stripped)
                or _BILAG.match(stripped)
                or _TABEL.match(stripped)
                or _PARA.match(stripped)
            )
            if not m:
                continue
            head = (m.group("head") or "").strip() or stripped
            out.append(
                Clause(
                    clause_ref=_normalise_ref(m.group("ref")),
                    heading=head[:200],
                    page_pdf=page.page_pdf,
                    char_start=start,
                    char_end=end,
                )
            )
    return out


def coverage(pages: list[PageText], clauses: list[Clause]) -> float:
    """Share of pages with at least one clause — the number ADR-0005 says to measure."""
    if not pages:
        return 0.0
    hit = {c.page_pdf for c in clauses}
    return len(hit) / len(pages)
