"""Storage facade — ported from bidflow ADR-0007 (ADR-0025 here).

Call sites never touch a provider: they use save / read_bytes / exists /
delete_prefix / materialize on this module. The backend is chosen by
settings.storage_backend:

  local  filesystem under storage_root — dev, test, CI. Refused outside dev/test
         by app.core.config (bidflow ADR-0083 must not repeat).
  s3     Hetzner Object Storage (ADR-0007 §3) — declared, not yet implemented;
         selecting it raises at first use with a pointer to ADR-0025.

Keys are `{org}/{contract}/{document}/{version_no}/{filename}` (ADR-0006): tenancy
and version are legible from the key alone.

`materialize(key)` is the crux (bidflow 0007): PyMuPDF and LibreOffice need a
real local path. For `local` it yields the file itself; for an object store it
downloads to a temp file and cleans up on exit — so ingest code is byte-identical
across backends.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from app.core.config import settings

_SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def safe_filename(name: str) -> str:
    """Strip path components and anything outside a conservative charset."""
    base = os.path.basename(name.replace("\\", "/")) or "fil"
    cleaned = "".join(c if c in _SAFE else "_" for c in base).strip("._") or "fil"
    return cleaned[:120]


class LocalBackend:
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents and p != self.root:
            raise ValueError(f"storage key escapes root: {key!r}")
        return p

    def save(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, p)  # atomic on the same filesystem

    def read_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete_prefix(self, prefix: str) -> int:
        p = self._path(prefix)
        if not p.exists():
            return 0
        n = sum(1 for f in p.rglob("*") if f.is_file())
        shutil.rmtree(p)
        return n

    @contextmanager
    def materialize(self, key: str) -> Generator[Path, None, None]:
        yield self._path(key)


_S3_TODO = "STORAGE_BACKEND=s3 is decided (ADR-0007 §3) but not built yet — see ADR-0025"


class S3Backend:
    """Placeholder until the Hetzner Object Storage backend lands (ADR-0025).
    Same surface as LocalBackend so call sites type-check against both."""

    def save(self, key: str, data: bytes) -> None:
        raise NotImplementedError(_S3_TODO)

    def read_bytes(self, key: str) -> bytes:
        raise NotImplementedError(_S3_TODO)

    def exists(self, key: str) -> bool:
        raise NotImplementedError(_S3_TODO)

    def delete_prefix(self, prefix: str) -> int:
        raise NotImplementedError(_S3_TODO)

    @contextmanager
    def materialize(self, key: str) -> Generator[Path, None, None]:
        raise NotImplementedError(_S3_TODO)
        yield Path()  # pragma: no cover — keeps the generator signature


def _backend() -> LocalBackend | S3Backend:
    if settings.storage_backend == "local":
        return LocalBackend(settings.storage_root)
    return S3Backend()


# ---- module-level facade (what call sites use) ---------------------------------------


def save(key: str, data: bytes) -> None:
    _backend().save(key, data)


def read_bytes(key: str) -> bytes:
    return _backend().read_bytes(key)


def exists(key: str) -> bool:
    return _backend().exists(key)


def delete_prefix(prefix: str) -> int:
    return _backend().delete_prefix(prefix)


@contextmanager
def materialize(key: str) -> Generator[Path, None, None]:
    with _backend().materialize(key) as p:
        yield p


@contextmanager
def scratch_dir() -> Generator[Path, None, None]:
    """A temp directory for conversion output, cleaned on exit."""
    d = Path(tempfile.mkdtemp(prefix="obliance-"))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)
