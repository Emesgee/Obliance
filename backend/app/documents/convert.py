"""docx/xlsx/pptx → PDF via LibreOffice headless — ported from bidflow ADR-0049.

Per-run user profile so concurrent conversions don't fight over LibreOffice's
profile lock. Returns None (never raises) when soffice is missing or fails:
the version then stays without a rendered PDF and is reported as such.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from app.core.config import settings

log = logging.getLogger(__name__)

CONVERTIBLE_MIMES: frozenset[str] = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # docx
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # xlsx
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # pptx
        "application/msword",
        "application/vnd.ms-excel",
        "application/rtf",
        "application/vnd.oasis.opendocument.text",
    }
)

_WINDOWS_CANDIDATES = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
]


def find_soffice() -> str | None:
    if settings.soffice_path and Path(settings.soffice_path).is_file():
        return settings.soffice_path
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    for c in _WINDOWS_CANDIDATES:
        if Path(c).is_file():
            return c
    return None


def to_pdf(src: Path, out_dir: Path, timeout_s: int = 180) -> Path | None:
    soffice = find_soffice()
    if soffice is None:
        log.warning("LibreOffice not found — cannot convert %s (ADR-0049)", src.name)
        return None
    profile = out_dir / "lo-profile"
    profile.mkdir(parents=True, exist_ok=True)
    cmd = [
        soffice,
        "--headless",
        "--norestore",
        f"-env:UserInstallation={profile.resolve().as_uri()}",
        "--convert-to",
        "pdf",
        "--outdir",
        str(out_dir),
        str(src),
    ]
    try:
        subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            timeout=timeout_s,
            env={**os.environ, "HOME": str(out_dir)},
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        log.warning("LibreOffice conversion failed for %s: %s", src.name, e)
        return None
    pdf = out_dir / (src.stem + ".pdf")
    return pdf if pdf.is_file() else None
