"""PDF page text + printed page numbers (bidflow ADR-0061/0062, ADR-0005 §2).

No model is involved. PyMuPDF reads the text layer; a scanned PDF yields empty
pages and is reported as such (OCR is opt-in per file — bidflow ADR-0023, later).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf

# "Side 12 af 40", "Side 12", "12 / 40", "12/40", "- 12 -", or a bare integer on
# its own line in the header/footer band.
_PRINTED = re.compile(
    r"^\s*(?:side\s+(?P<a>\d{1,4})(?:\s+af\s+\d{1,4})?"
    r"|(?P<b>\d{1,4})\s*/\s*\d{1,4}"
    r"|-\s*(?P<c>\d{1,4})\s*-"
    r"|(?P<d>\d{1,4}))\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class PageText:
    page_pdf: int  # 1-based
    text: str
    page_printed: str | None


def _printed_number(text: str) -> str | None:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    band = lines[:2] + lines[-2:]
    for ln in band:
        m = _PRINTED.match(ln)
        if m:
            return next(v for v in m.groupdict().values() if v)
    return None


def extract_pages(path: Path) -> list[PageText]:
    pages: list[PageText] = []
    with pymupdf.open(path) as doc:  # type: ignore[no-untyped-call]
        for i in range(doc.page_count):
            text = str(doc[i].get_text("text") or "")
            pages.append(PageText(page_pdf=i + 1, text=text, page_printed=_printed_number(text)))
    return pages


def looks_scanned(pages: list[PageText]) -> bool:
    """No usable text layer on (almost) every page → needs OCR (opt-in, later)."""
    if not pages:
        return False
    empty = sum(1 for p in pages if len(p.text.strip()) < 20)
    return empty >= max(1, int(0.9 * len(pages)))
