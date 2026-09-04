"""POST /api/auth/login · GET /api/me — ADR-0024 increment 1.

Login is rate-limited per client IP (bidflow ADR-0006/0009). Errors are Danish
and carry a machine `code` (bidflow ADR-0036). A wrong email and a wrong
password return the same message — no user enumeration.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import LoginIn, MeOut, TokenOut
from app.core import audit
from app.core.auth import Principal, current_principal, resolve_membership, touch_last_login
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.rate_limit import rate_limited
from app.core.rls import tenant
from app.core.security import issue_access_token, verify_password
from app.domain.models import ActorType, AuditAction, Organization, Profile

router = APIRouter(prefix="/api", tags=["auth"])

_BAD_CREDENTIALS = {"error": "Forkert e-mail eller adgangskode", "code": "bad_credentials"}


@router.post(
    "/auth/login",
    response_model=TokenOut,
    dependencies=[Depends(rate_limited("login", lambda: settings.ratelimit_login))],
)
def login(body: LoginIn) -> TokenOut:
    # Identity tables have no RLS (ADR-0002): this runs before any tenant exists.
    with SessionLocal() as s:
        user = s.scalars(
            select(Profile).where(func.lower(Profile.email) == body.email.lower())
        ).first()
        if user is None or not verify_password(body.password, user.password_hash):
            _audit_failed_login(s, user, body.email)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=_BAD_CREDENTIALS)
        if user.deactivated_at is not None:
            _audit_failed_login(s, user, body.email, reason="deactivated")
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                detail={"error": "Kontoen er deaktiveret", "code": "account_deactivated"},
            )
        mem = resolve_membership(s, user)
        if mem is None:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail={"error": "Ingen organisation", "code": "no_org"},
            )
        touch_last_login(s, user)
        # ADR-0011 §3: access events go in the customer's log. The org's RLS context
        # is needed to write there; the membership just resolved provides it.
        with tenant(mem.organization_id, user_id=user.id, role=mem.role.value, session=s):
            audit.record(
                s,
                org_id=mem.organization_id,
                action=AuditAction.login,
                actor=audit.Actor(ActorType.human, user.name, user.id, mem.role.value),
                object_kind="profile",
                object_id=user.id,
                object_label=user.email,
            )
        s.commit()
        token = issue_access_token(user_id=user.id, org_id=mem.organization_id, role=mem.role.value)
    return TokenOut(access_token=token)


def _audit_failed_login(
    s: Session, user: Profile | None, email: str, *, reason: str = "bad_credentials"
) -> None:
    """ADR-0011 afklaring 1: failed attempts are logged — when the email maps to a
    known member, in that member's org (an unknown email has no org to log to)."""
    if user is None:
        return
    mem = resolve_membership(s, user)
    if mem is None:
        return
    with tenant(mem.organization_id, user_id=user.id, role=mem.role.value, session=s):
        audit.record(
            s,
            org_id=mem.organization_id,
            action=AuditAction.login_failed,
            actor=audit.Actor(ActorType.human, user.name, user.id, mem.role.value),
            object_kind="profile",
            object_id=user.id,
            object_label=email,
            details={"reason": reason},
        )
    s.commit()


@router.get("/me", response_model=MeOut)
def me(principal: Principal = Depends(current_principal)) -> MeOut:
    with SessionLocal() as s:
        org = s.get(Organization, principal.org_id)
        org_name = org.name if org else ""
    return MeOut(
        user_id=principal.user_id,
        email=principal.email,
        name=principal.name,
        org_id=principal.org_id,
        org_name=org_name,
        role=principal.role,
        permissions=sorted(principal.permissions),
    )
