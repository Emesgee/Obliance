"""Citation verification — ADR-0005 §3: locate the quote, do not trust the page.

    locate(pages, clauses, quote, claimed_page) -> Located

Exact (whitespace/case-normalised) match first, on the claimed page then all
pages; then bidflow 0055's fallback — word overlap per page — for paraphrased
quotes. Not found → verified=False, and the caller caps confidence to `lav`.
Pure: takes page/clause tuples, so it is unit-testable without a database.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.domain.models import Confidence


@dataclass(frozen=True, slots=True)
class Page:
    page_pdf: int
    page_printed: str | None
    text: str


@dataclass(frozen=True, slots=True)
class Clause:
    clause_ref: str
    page_pdf: int
    char_start: int


@dataclass(frozen=True, slots=True)
class Located:
    verified: bool
    page_pdf: int | None
    page_printed: str | None
    clause_ref: str | None


_WS = re.compile(r"\s+")
_WORD = re.compile(r"[a-zA-ZæøåÆØÅ0-9]{4,}")

OVERLAP_THRESHOLD = 0.7


def _norm(s: str) -> str:
    return _WS.sub(" ", s.replace("­", "")).strip().lower()


def _clause_at(clauses: Sequence[Clause], page: int, pos: int | None) -> str | None:
    on_page = [c for c in clauses if c.page_pdf == page]
    if not on_page:
        return None
    if pos is None:
        return None  # overlap fallback: page known, position not — no guessing (ADR-0005 §3)
    before = [c for c in on_page if c.char_start <= pos]
    return before[-1].clause_ref if before else None  # text above the first heading has no clause


def locate(
    pages: Sequence[Page],
    clauses: Sequence[Clause],
    quote: str,
    claimed_page: int | None,
) -> Located:
    q = _norm(quote)
    if not q:
        return Located(False, claimed_page, None, None)
    ordered = sorted(pages, key=lambda p: (p.page_pdf != claimed_page, p.page_pdf))
    for p in ordered:
        pos = _norm(p.text).find(q)
        if pos >= 0:
            # pos is in normalised text; clause offsets are in raw text. Map back
            # approximately by scaling — good enough for "nearest preceding heading".
            raw_pos = int(pos * (len(p.text) / max(len(_norm(p.text)), 1)))
            return Located(
                True, p.page_pdf, p.page_printed, _clause_at(clauses, p.page_pdf, raw_pos)
            )
    words = set(_WORD.findall(q))
    if len(words) >= 3:
        best: tuple[float, Page | None] = (0.0, None)
        for p in ordered:
            pw = set(_WORD.findall(_norm(p.text)))
            share = len(words & pw) / len(words)
            if share > best[0]:
                best = (share, p)
        if best[1] is not None and best[0] >= OVERLAP_THRESHOLD:
            p = best[1]
            return Located(True, p.page_pdf, p.page_printed, _clause_at(clauses, p.page_pdf, None))
    return Located(False, claimed_page, None, None)


def label(
    doc_title: str, page_pdf: int | None, page_printed: str | None, clause_ref: str | None
) -> str:
    """'Hovedkontrakt · s. 12 · pkt. 8.2' — derived, never edited (ADR-0005 §1)."""
    parts = [doc_title]
    page = page_printed or (str(page_pdf) if page_pdf else None)
    if page:
        parts.append(f"s. {page}")
    if clause_ref:
        parts.append(f"pkt. {clause_ref}" if clause_ref[0].isdigit() else clause_ref)
    return " · ".join(parts)


def citation_json(
    *,
    document_id: Any,
    document_version_id: Any,
    doc_title: str,
    quote: str,
    located: Located,
) -> dict[str, Any]:
    """The JSONB shape stored on ai_suggestions.citations (ADR-0004/0005)."""
    return {
        "kind": "document",
        "document_id": str(document_id),
        "document_version_id": str(document_version_id),
        "page_pdf": located.page_pdf,
        "page_printed": located.page_printed,
        "clause_ref": located.clause_ref,
        "quote": quote,
        "verified": located.verified,
        "label": label(doc_title, located.page_pdf, located.page_printed, located.clause_ref),
    }


_ORDER = {Confidence.hoej: 2, Confidence.mellem: 1, Confidence.lav: 0}


def cap(confidence: Confidence, *, all_verified: bool) -> Confidence:
    """ADR-0005 §3: an unverified citation caps the suggestion at `lav`."""
    return confidence if all_verified else Confidence.lav


def lowest(*cs: Confidence) -> Confidence:
    return min(cs, key=lambda c: _ORDER[c]) if cs else Confidence.lav
