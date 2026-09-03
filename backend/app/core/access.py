"""RBAC as data — ADR-0003.

The matrix below is the single source: migration 0002 seeds `role_permissions`
from it, and tests assert the table and this constant never drift. Row level
(which rows a user sees) is the database's job (ADR-0002); this module answers
field-, module- and action-level questions.

Permissions use the mockup's names (snake_case) plus three that follow from
ADR-0002: kontrakt_laes (everyone), fortrolig_tildel (grant access to a
fortrolig contract), fortrolig_laes_alle (auditor reads everything in its org).
"""

from __future__ import annotations

from typing import Final

from app.domain.models import MemberRole

# ---- permissions ---------------------------------------------------------------
KONTRAKT_LAES: Final = "kontrakt_laes"
KONTRAKT_RED: Final = "kontrakt_red"
ARKIVER: Final = "arkiver"
HITL: Final = "hitl"
OKONOMI: Final = "okonomi"
RACI_GODKEND: Final = "raci_godkend"
BRUGERE: Final = "brugere"
AGENTER: Final = "agenter"
EKSPORT: Final = "eksport"
AUDIT: Final = "audit"
FORTROLIG_TILDEL: Final = "fortrolig_tildel"
FORTROLIG_LAES_ALLE: Final = "fortrolig_laes_alle"

ALL_PERMISSIONS: Final[frozenset[str]] = frozenset(
    {
        KONTRAKT_LAES,
        KONTRAKT_RED,
        ARKIVER,
        HITL,
        OKONOMI,
        RACI_GODKEND,
        BRUGERE,
        AGENTER,
        EKSPORT,
        AUDIT,
        FORTROLIG_TILDEL,
        FORTROLIG_LAES_ALLE,
    }
)

# ---- the matrix (ADR-0003 §1 + ADR-0002 afklaringer) -------------------------------
_M = MemberRole
MATRIX: Final[dict[MemberRole, frozenset[str]]] = {
    _M.systemadministrator: frozenset(ALL_PERMISSIONS - {FORTROLIG_LAES_ALLE}),
    _M.contract_manager: frozenset(
        {
            KONTRAKT_LAES,
            KONTRAKT_RED,
            ARKIVER,
            HITL,
            RACI_GODKEND,
            AGENTER,
            EKSPORT,
            AUDIT,
            FORTROLIG_TILDEL,
        }
    ),
    _M.contract_owner: frozenset(
        {KONTRAKT_LAES, HITL, OKONOMI, RACI_GODKEND, EKSPORT, AUDIT, FORTROLIG_TILDEL}
    ),
    _M.procurement_manager: frozenset({KONTRAKT_LAES, KONTRAKT_RED, HITL, EKSPORT}),
    _M.legal_compliance: frozenset({KONTRAKT_LAES, HITL, RACI_GODKEND, EKSPORT, AUDIT}),
    _M.finance_controller: frozenset({KONTRAKT_LAES, HITL, OKONOMI, EKSPORT}),
    _M.business_user: frozenset({KONTRAKT_LAES}),
    _M.auditor: frozenset({KONTRAKT_LAES, EKSPORT, AUDIT, FORTROLIG_LAES_ALLE}),
}


def permissions_for(role: MemberRole | str) -> frozenset[str]:
    return MATRIX[MemberRole(role)]


def can(role: MemberRole | str, permission: str) -> bool:
    if permission not in ALL_PERMISSIONS:
        raise ValueError(f"unknown permission: {permission}")
    return permission in permissions_for(role)


def seed_rows() -> list[tuple[str, str]]:
    """(role, permission) pairs for the migration seed — deterministic order."""
    return sorted((r.value, p) for r, perms in MATRIX.items() for p in perms)
