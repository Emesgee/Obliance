"""Request-side auth: Bearer token → Principal → tenant-scoped Session.

Ported from bidflow's `auth_and_org` decorator (ADR-0006/0065/0067/0068) into
FastAPI dependencies:

    principal = Depends(current_principal)          # who, in which org, which role
    session   = Depends(tenant_session)             # DB session INSIDE tenant()
    _         = Depends(require("kontrakt_red"))    # RBAC gate (ADR-0003)

There is no way to get a Session for a data route without a Principal — the
tenant context (ADR-0002) is not optional, it is the dependency graph.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Generator
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import access
from app.core.db import SessionLocal, session_scope
from app.core.rls import bind_session, tenant
from app.core.security import TokenError, decode_token
from app.domain.models import MemberRole, Organization, OrganizationMember, Profile

_bearer = HTTPBearer(auto_error=False)


def _unauthorized(msg: str, code: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": msg, "code": code},
        headers={"WWW-Authenticate": "Bearer"},
    )


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: MemberRole
    email: str
    name: str

    @property
    def permissions(self) -> frozenset[str]:
        return access.permissions_for(self.role)

    def can(self, permission: str) -> bool:
        return access.can(self.role, permission)


def resolve_membership(session: Session, user: Profile) -> OrganizationMember | None:
    """v1 (as bidflow): the user's first membership. An org switcher can come later."""
    return session.scalars(
        select(OrganizationMember)
        .where(OrganizationMember.profile_id == user.id)
        .order_by(OrganizationMember.created_at)
    ).first()


def current_principal(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    if creds is None or creds.scheme.lower() != "bearer":
        raise _unauthorized("Ikke autoriseret", "unauthorized")
    try:
        claims = decode_token(creds.credentials)
    except TokenError as e:
        raise _unauthorized("Sessionen er udløbet — log ind igen", f"token_{e}") from e
    if claims.scope != "access":
        # A step-up (pending MFA) token proves password only (bidflow ADR-0065).
        raise _unauthorized("MFA ikke fuldført", "mfa_incomplete")

    # Identity tables carry no RLS (ADR-0002) — safe to read before set_tenant.
    with SessionLocal() as s:
        user = s.get(Profile, claims.user_id)
        if user is None:
            raise _unauthorized("Ikke autoriseret", "unauthorized")
        if user.deactivated_at is not None:
            # Soft deactivation revokes access even on an already-issued token.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "Kontoen er deaktiveret", "code": "account_deactivated"},
            )
        mem = s.execute(
            select(OrganizationMember).where(
                OrganizationMember.profile_id == user.id,
                OrganizationMember.organization_id == claims.org_id,
            )
        ).scalar_one_or_none()
        if mem is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "Ingen organisation", "code": "no_org"},
            )
        org = s.get(Organization, claims.org_id)
        if org is None or org.deleted_at is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "Organisationen er lukket", "code": "organization_deleted"},
            )
        # The role in the token is a hint; the membership row is the truth
        # (a role change takes effect on the next request, not at next login).
        return Principal(
            user_id=user.id, org_id=org.id, role=mem.role, email=user.email, name=user.name
        )


def tenant_session(
    principal: Principal = Depends(current_principal),
) -> Generator[Session, None, None]:
    """A Session whose every transaction carries the three RLS GUCs (ADR-0002).

    The context is bound to the Session object (bind_session), not only to the
    ContextVar: FastAPI runs this dependency and the endpoint in different
    thread contexts, so the ambient variable alone would not reach the query.
    """
    with tenant(principal.org_id, user_id=principal.user_id, role=principal.role.value) as ctx:
        with session_scope() as s:
            bind_session(s, ctx)
            yield s


def require(permission: str) -> Callable[[Principal], Principal]:
    """RBAC gate (ADR-0003): 403 unless the principal's role grants `permission`."""
    if permission not in access.ALL_PERMISSIONS:
        raise ValueError(f"unknown permission: {permission}")

    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.can(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": "Du har ikke rettighed til denne handling", "code": "forbidden"},
            )
        return principal

    return dependency


def touch_last_login(session: Session, user: Profile) -> None:
    user.last_login_at = datetime.now(UTC)
    session.add(user)
