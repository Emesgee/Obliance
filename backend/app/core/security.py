"""Password hashing and stateless access tokens (ADR-0024).

- argon2id via pwdlib (replaces bidflow's passlib+bcrypt and its 72-byte pin).
- HS256 JWT via PyJWT, signed with settings.secret_key. Claims: sub (profile id),
  org (organization id), role (member_role), exp. No server-side session — the
  same stateless design as bidflow ADR-0065, so a later MFA step-up token is just
  a short-lived JWT with scope="mfa".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash

from app.core.config import settings

_hasher = PasswordHash.recommended()

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return _hasher.verify(password, password_hash)
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class TokenClaims:
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: str
    scope: str  # "access" | "mfa" (step-up, reserved for increment 2)


class TokenError(ValueError):
    pass


def issue_access_token(
    *, user_id: uuid.UUID, org_id: uuid.UUID, role: str, ttl_minutes: int | None = None
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "org": str(org_id),
        "role": role,
        "scope": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl_minutes or settings.jwt_ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str) -> TokenClaims:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as e:
        raise TokenError("expired") from e
    except jwt.InvalidTokenError as e:
        raise TokenError("invalid") from e
    try:
        return TokenClaims(
            user_id=uuid.UUID(str(payload["sub"])),
            org_id=uuid.UUID(str(payload["org"])),
            role=str(payload["role"]),
            scope=str(payload.get("scope", "access")),
        )
    except (KeyError, ValueError) as e:
        raise TokenError("malformed") from e
