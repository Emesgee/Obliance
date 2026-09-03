"""GET/POST /api/contracts — the first data route, and the proof that the chain
Bearer → Principal → tenant() → RLS holds over HTTP.

Row visibility is the database's (ADR-0002): the list query has NO
organization_id filter on purpose. Field visibility is ours (ADR-0003 §2):
financial fields are dropped from the response when the caller lacks `okonomi`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.schemas import ContractCreate, ContractListOut, ContractOut
from app.core import access
from app.core.auth import Principal, current_principal, require, tenant_session
from app.domain.models import Confidentiality, Contract

router = APIRouter(prefix="/api/contracts", tags=["contracts"])


def mask_financials(out: ContractOut, principal: Principal) -> ContractOut:
    """ADR-0003 §2: without `okonomi` the amounts are not in the response —
    None, not 0, so a client cannot mistake 'hidden' for 'zero'."""
    if principal.can(access.OKONOMI):
        return out
    return out.model_copy(update={"total_value": None, "annual_value": None})


@router.get("", response_model=ContractListOut)
def list_contracts(
    principal: Principal = Depends(current_principal),
    session: Session = Depends(tenant_session),
) -> ContractListOut:
    rows = session.scalars(select(Contract).order_by(Contract.reference)).all()
    total = session.scalar(select(func.count()).select_from(Contract)) or 0
    items = [mask_financials(ContractOut.model_validate(r), principal) for r in rows]
    return ContractListOut(items=items, total=int(total))


@router.post("", response_model=ContractOut, status_code=status.HTTP_201_CREATED)
def create_contract(
    body: ContractCreate,
    principal: Principal = Depends(require(access.KONTRAKT_RED)),
    session: Session = Depends(tenant_session),
) -> ContractOut:
    data = body.model_dump()
    # A fortrolig contract must be visible to its creator on INSERT ... RETURNING
    # (contract_visibility is a SELECT policy — see migration 0001 docstring).
    # If neither owner nor manager is given, the creator becomes manager.
    if (
        data["confidentiality"] == Confidentiality.fortrolig
        and data.get("owner_id") is None
        and data.get("manager_id") is None
    ):
        data["manager_id"] = principal.user_id
    if not principal.can(access.OKONOMI):
        # No okonomi → cannot set amounts either (symmetry with masking).
        data["total_value"] = None
        data["annual_value"] = None

    contract = Contract(
        organization_id=principal.org_id,
        created_by=principal.user_id,
        updated_at=datetime.now(UTC),
        **data,
    )
    session.add(contract)
    try:
        session.flush()
    except IntegrityError as e:
        session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"error": "Referencen findes allerede", "code": "reference_taken"},
        ) from e
    session.refresh(contract)
    return mask_financials(ContractOut.model_validate(contract), principal)
