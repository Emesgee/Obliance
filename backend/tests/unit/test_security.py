"""ADR-0024 (tokens, hashing) and ADR-0003 (matrix as data) — pure, no DB."""

from __future__ import annotations

import uuid

import pytest

from app.core import access
from app.core.security import (
    TokenError,
    decode_token,
    hash_password,
    issue_access_token,
    verify_password,
)
from app.domain.models import MemberRole


def test_password_roundtrip_and_rejects_wrong():
    h = hash_password("korrekt-adgangskode-123")
    assert h != "korrekt-adgangskode-123"
    assert verify_password("korrekt-adgangskode-123", h)
    assert not verify_password("forkert", h)
    assert not verify_password("noget", None)


def test_token_roundtrip():
    uid, oid = uuid.uuid4(), uuid.uuid4()
    tok = issue_access_token(user_id=uid, org_id=oid, role="auditor")
    claims = decode_token(tok)
    assert (claims.user_id, claims.org_id, claims.role, claims.scope) == (
        uid,
        oid,
        "auditor",
        "access",
    )


def test_expired_token_is_rejected():
    tok = issue_access_token(
        user_id=uuid.uuid4(), org_id=uuid.uuid4(), role="auditor", ttl_minutes=-1
    )
    with pytest.raises(TokenError, match="expired"):
        decode_token(tok)


def test_tampered_token_is_rejected():
    tok = issue_access_token(user_id=uuid.uuid4(), org_id=uuid.uuid4(), role="auditor")
    with pytest.raises(TokenError):
        decode_token(tok[:-3] + "abc")


# ---- ADR-0003: the matrix is exactly the mockup's 8 × 9, plus ADR-0002's three ------


def test_matrix_covers_all_roles():
    assert set(access.MATRIX) == set(MemberRole)


def test_matrix_matches_adr_0003():
    m = access.MATRIX
    R = MemberRole
    # Every role can read contracts (ADR-0002 afklaring 1: Business User sees intern).
    assert all(access.KONTRAKT_LAES in m[r] for r in R)
    # Mockup rows (kontraktRed, arkiver, hitl, okonomi, raciGodkend, brugere, agenter, eksport, audit)
    assert m[R.business_user] == {access.KONTRAKT_LAES}
    assert m[R.auditor] == {
        access.KONTRAKT_LAES,
        access.EKSPORT,
        access.AUDIT,
        access.FORTROLIG_LAES_ALLE,
    }
    assert access.OKONOMI not in m[R.contract_manager]
    assert access.OKONOMI in m[R.contract_owner]
    assert access.OKONOMI in m[R.finance_controller]
    assert access.BRUGERE in m[R.systemadministrator]
    assert all(access.BRUGERE not in m[r] for r in R if r != R.systemadministrator)
    # ADR-0002 afklaring 2: who may grant access to fortrolig contracts.
    granters = {r for r in R if access.FORTROLIG_TILDEL in m[r]}
    assert granters == {R.contract_owner, R.contract_manager, R.systemadministrator}
    # Only the auditor reads everything via role.
    assert {r for r in R if access.FORTROLIG_LAES_ALLE in m[r]} == {R.auditor}


def test_unknown_permission_raises():
    with pytest.raises(ValueError):
        access.can(MemberRole.auditor, "does_not_exist")
