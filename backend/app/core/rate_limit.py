"""Rate limiting on top of `limits` — ported from bidflow ADR-0009.

`limits` is the engine Flask-Limiter wraps; we use it directly (no framework
glue, no `rich` dependency). Storage is memory:// in dev/test and redis:// in
prod so a limit holds across api replicas (ADR-0007). Limit strings are read at
request time through a callable, so tests can loosen/tighten them at runtime.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException, Request
from limits import parse
from limits.storage import storage_from_string
from limits.strategies import FixedWindowRateLimiter

from app.core.config import settings


class RateLimiter:
    def __init__(self) -> None:
        self._storage = storage_from_string(settings.ratelimit_storage_uri)
        self._strategy = FixedWindowRateLimiter(self._storage)

    def hit(self, bucket: str, limit_value: str, key: str) -> bool:
        if not settings.ratelimit_enabled:
            return True
        return self._strategy.hit(parse(limit_value), bucket, key)

    def reset(self) -> None:
        """Clear all counters (between tests)."""
        try:
            self._storage.reset()
        except Exception:
            self._storage = storage_from_string(settings.ratelimit_storage_uri)
            self._strategy = FixedWindowRateLimiter(self._storage)


limiter = RateLimiter()


def _client_ip(request: Request) -> str:
    # Caddy sets X-Forwarded-For (ADR-0007); uvicorn is started with
    # --proxy-headers so request.client already reflects it.
    return request.client.host if request.client else "unknown"


def rate_limited(bucket: str, limit_value: Callable[[], str]) -> Callable[[Request], None]:
    """Dependency factory: `Depends(rate_limited("login", lambda: settings.ratelimit_login))`."""

    def dependency(request: Request) -> None:
        if not limiter.hit(bucket, limit_value(), _client_ip(request)):
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "For mange forespørgsler. Prøv igen om lidt.",
                    "code": "rate_limited",
                },
            )

    return dependency
