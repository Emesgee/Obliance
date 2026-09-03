#!/usr/bin/env python3
"""CI gates that need no database — ADR-0023 §5.

Pure stdlib. Exit code 1 on any finding. Run all gates or name them:

    python scripts/gates.py            # all
    python scripts/gates.py g01 g04    # subset

  G-01  no LLM-provider URL or key name under frontend/            (ADR-0008)
  G-02  no hex colour outside frontend/src/tokens.css              (ADR-0015)
  G-04  only backend/app/llm imports `anthropic`; no model id
        outside backend/app/llm/config.py                          (ADR-0009)
  G-14  no temperature / top_p / top_k / budget_tokens in app/llm  (ADR-0009)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
BACKEND_APP = ROOT / "backend" / "app"

SKIP_DIRS = {"node_modules", "dist", ".venv", "__pycache__", "generated", ".git"}
TEXT_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".py", ".json", ".md"}


def _files(base: Path, suffixes: set[str] | None = None) -> list[Path]:
    if not base.exists():
        return []
    out = []
    for p in base.rglob("*"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.is_file() and (suffixes is None or p.suffix in suffixes):
            out.append(p)
    return out


def _grep(files: list[Path], pattern: re.Pattern[str]) -> list[str]:
    hits = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{f.relative_to(ROOT)}:{n}: {line.strip()[:120]}")
    return hits


def g01() -> list[str]:
    pat = re.compile(r"api\.anthropic\.com|x-api-key|ANTHROPIC_", re.IGNORECASE)
    return _grep(_files(FRONTEND, TEXT_SUFFIXES), pat)


def g02() -> list[str]:
    # Hex colours belong in tokens.css only. Everything else reads a CSS variable.
    pat = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b")
    files = [
        f for f in _files(FRONTEND / "src", {".ts", ".tsx", ".css", ".html"})
        if f.name != "tokens.css"
    ]
    hits = []
    for h in _grep(files, pat):
        # allow anchors like "#root" / "#app" (id selectors) — hex needs digits or a-f only
        m = re.search(r"#([0-9a-fA-F]{3,8})\b", h.split(": ", 1)[1])
        if m and not re.fullmatch(r"[0-9a-fA-F]{3,8}", m.group(1)):
            continue
        if m and m.group(1).lower() in {"root", "app"}:
            continue
        hits.append(h)
    return hits


def g04() -> list[str]:
    hits = []
    llm_dir = BACKEND_APP / "llm"
    imp = re.compile(r"^\s*(import\s+anthropic|from\s+anthropic\b)")
    for f in _files(BACKEND_APP, {".py"}):
        if llm_dir in f.parents:
            continue
        hits += _grep([f], imp)
    # model ids: allowed only in app/llm/config.py
    model = re.compile(r"claude-(?:opus|sonnet|haiku|fable|mythos)-[0-9][a-z0-9-]*")
    for f in _files(BACKEND_APP, {".py"}):
        if f == llm_dir / "config.py":
            continue
        hits += _grep([f], model)
    return hits


def g14() -> list[str]:
    pat = re.compile(r"\b(temperature|top_p|top_k|budget_tokens)\s*[=:]")
    return _grep(_files(BACKEND_APP / "llm", {".py"}), pat)


GATES = {"g01": g01, "g02": g02, "g04": g04, "g14": g14}


def main(argv: list[str]) -> int:
    names = argv or list(GATES)
    failed = 0
    for name in names:
        fn = GATES.get(name)
        if fn is None:
            print(f"unknown gate: {name}", file=sys.stderr)
            return 2
        hits = fn()
        status = "OK " if not hits else "FAIL"
        print(f"[{status}] {name.upper()} — {fn.__doc__ or ''}".rstrip())
        for h in hits:
            print(f"    {h}")
        failed += bool(hits)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
