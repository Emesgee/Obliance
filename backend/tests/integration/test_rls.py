"""Gate G-05 — RLS on two levels, against real Postgres as the app role.

Level 1 (ADR-0002 / bidflow 0004): even with NO organization_id filter, the
database returns and accepts only the active tenant's rows.

Level 2 (ADR-0002): a fortrolig contract is visible only to its owner/manager,
to people on contract_access, to the auditor role, and to system context.
Children (contract_budgets) inherit that visibility via contract_scope.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.rls import tenant
from app.domain.models import Contract, ContractAccess, ContractBudget

pytestmark = pytest.mark.integration


# ---- level 1 -----------------------------------------------------------------


def test_no_context_returns_nothing(Session_, make_org, make_contract):
    org_a = make_org("A")
    make_contract(org_a, "K-1")
    with Session_() as s:
        assert s.scalars(select(Contract)).all() == []


def test_unfiltered_query_only_sees_own_org(Session_, make_org, make_user, make_contract):
    org_a, org_b = make_org("A"), make_org("B")
    ua = make_user(org_a, "a@test.dk", "contract_manager")
    make_contract(org_a, "K-A")
    make_contract(org_b, "K-B")
    with tenant(org_a, user_id=ua), Session_() as s:
        rows = s.scalars(select(Contract)).all()  # NOTE: no .where(organization_id)
        assert [r.reference for r in rows] == ["K-A"]


def test_cross_org_row_hidden_by_id(Session_, make_org, make_user, make_contract):
    org_a, org_b = make_org("A"), make_org("B")
    ua = make_user(org_a, "a@test.dk", "contract_manager")
    cid_b = make_contract(org_b, "K-B")
    with tenant(org_a, user_id=ua), Session_() as s:
        assert s.get(Contract, cid_b) is None


def test_cross_org_write_blocked(Session_, make_org, make_user):
    org_a, org_b = make_org("A"), make_org("B")
    ua = make_user(org_a, "a@test.dk", "contract_manager")
    with tenant(org_a, user_id=ua), Session_() as s:
        s.add(Contract(organization_id=org_b, reference="probe", name="probe"))
        with pytest.raises(DBAPIError):
            s.flush()
        s.rollback()


# ---- level 2 -----------------------------------------------------------------


def test_intern_contract_visible_to_any_member(Session_, make_org, make_user, make_contract):
    org = make_org("A")
    bu = make_user(org, "bu@test.dk", "business_user")
    make_contract(org, "K-INTERN", confidentiality="intern")
    with tenant(org, user_id=bu), Session_() as s:
        assert [c.reference for c in s.scalars(select(Contract))] == ["K-INTERN"]


def test_fortrolig_hidden_without_access(Session_, make_org, make_user, make_contract):
    org = make_org("A")
    bu = make_user(org, "bu@test.dk", "business_user")
    make_contract(org, "K-F", confidentiality="fortrolig")
    with tenant(org, user_id=bu), Session_() as s:
        assert s.scalars(select(Contract)).all() == []


def test_fortrolig_visible_to_owner_and_manager(Session_, make_org, make_user, make_contract):
    org = make_org("A")
    owner = make_user(org, "co@test.dk", "contract_owner")
    mgr = make_user(org, "cm@test.dk", "contract_manager")
    other = make_user(org, "x@test.dk", "contract_manager")
    make_contract(org, "K-F", confidentiality="fortrolig", owner_id=owner, manager_id=mgr)
    for uid in (owner, mgr):
        with tenant(org, user_id=uid), Session_() as s:
            assert [c.reference for c in s.scalars(select(Contract))] == ["K-F"]
    with tenant(org, user_id=other), Session_() as s:
        assert s.scalars(select(Contract)).all() == []


def test_fortrolig_visible_via_contract_access_until_revoked(
    Session_, make_org, make_user, make_contract
):
    org = make_org("A")
    bu = make_user(org, "bu@test.dk", "business_user")
    admin = make_user(org, "adm@test.dk", "systemadministrator")
    cid = make_contract(org, "K-F", confidentiality="fortrolig")

    # grant (as system — the service layer runs grants under the granting user,
    # but the row itself is org-scoped and needs no visibility of the contract)
    with tenant(org, system=True), Session_() as s:
        s.add(ContractAccess(organization_id=org, contract_id=cid, profile_id=bu, granted_by=admin))
        s.commit()

    with tenant(org, user_id=bu), Session_() as s:
        assert [c.reference for c in s.scalars(select(Contract))] == ["K-F"]

    with tenant(org, system=True), Session_() as s:
        s.execute(
            text("UPDATE contract_access SET revoked_at = now() WHERE contract_id = :c"), {"c": cid}
        )
        s.commit()

    with tenant(org, user_id=bu), Session_() as s:
        assert s.scalars(select(Contract)).all() == []


def test_auditor_sees_fortrolig_via_role(Session_, make_org, make_user, make_contract):
    org = make_org("A")
    aud = make_user(org, "aud@test.dk", "auditor")
    make_contract(org, "K-F", confidentiality="fortrolig")
    with tenant(org, user_id=aud, role="auditor"), Session_() as s:
        assert [c.reference for c in s.scalars(select(Contract))] == ["K-F"]
    # the same person WITHOUT the role GUC does not see it — the role is what grants it
    with tenant(org, user_id=aud), Session_() as s:
        assert s.scalars(select(Contract)).all() == []


def test_system_context_sees_everything_in_org_only(Session_, make_org, make_contract):
    org_a, org_b = make_org("A"), make_org("B")
    make_contract(org_a, "K-F", confidentiality="fortrolig")
    make_contract(org_b, "K-B", confidentiality="fortrolig")
    with tenant(org_a, system=True), Session_() as s:
        assert [c.reference for c in s.scalars(select(Contract))] == ["K-F"]


def test_child_table_inherits_visibility(Session_, make_org, make_user, make_contract):
    org = make_org("A")
    bu = make_user(org, "bu@test.dk", "business_user")
    owner = make_user(org, "co@test.dk", "contract_owner")
    cid = make_contract(org, "K-F", confidentiality="fortrolig", owner_id=owner)
    with tenant(org, system=True), Session_() as s:
        s.add(
            ContractBudget(
                contract_id=cid, organization_id=org, year=2026, budget=Decimal("100.00")
            )
        )
        s.commit()
    with tenant(org, user_id=owner), Session_() as s:
        assert len(s.scalars(select(ContractBudget)).all()) == 1
    with tenant(org, user_id=bu), Session_() as s:
        assert s.scalars(select(ContractBudget)).all() == []


def test_child_insert_blocked_for_invisible_contract(Session_, make_org, make_user, make_contract):
    org = make_org("A")
    bu = make_user(org, "bu@test.dk", "business_user")
    cid = make_contract(org, "K-F", confidentiality="fortrolig")
    with tenant(org, user_id=bu), Session_() as s:
        s.add(ContractBudget(contract_id=cid, organization_id=org, year=2026, budget=Decimal("1")))
        with pytest.raises(DBAPIError):
            s.flush()
        s.rollback()


def test_guc_does_not_leak_between_sessions(Session_, make_org, make_user, make_contract):
    """Transaction-local GUCs reset on commit — a pooled connection reused by a
    session with no context must see nothing."""
    org = make_org("A")
    ua = make_user(org, "a@test.dk", "contract_manager")
    make_contract(org, "K-1")
    with tenant(org, user_id=ua), Session_() as s:
        assert len(s.scalars(select(Contract)).all()) == 1
        s.commit()
    with Session_() as s:  # no context
        assert s.scalars(select(Contract)).all() == []


def test_unknown_org_uuid_sees_nothing(Session_, make_org, make_user, make_contract):
    org = make_org("A")
    ua = make_user(org, "a@test.dk", "contract_manager")
    make_contract(org, "K-1")
    with tenant(uuid.uuid4(), user_id=ua), Session_() as s:
        assert s.scalars(select(Contract)).all() == []
