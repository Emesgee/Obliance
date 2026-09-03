"""ADR-0024 over HTTP: login, /me, RBAC gate, rate limit — and G-05 through the
API: the contracts list is scoped by RLS with no filter in the query."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.core import access
from app.core.rate_limit import limiter
from app.domain.models import RolePermission

pytestmark = pytest.mark.integration

PW = "korrekt-adgangskode-123"


def _login(client: TestClient, email: str, password: str = PW) -> str:
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---- login ------------------------------------------------------------------------


def test_login_ok_and_me(client, make_org, make_user):
    org = make_org("Amgros")
    make_user(org, "cm@test.dk", "contract_manager", password=PW)
    tok = _login(client, "cm@test.dk")
    me = client.get("/api/me", headers=_auth(tok)).json()
    assert me["email"] == "cm@test.dk"
    assert me["role"] == "contract_manager"
    assert me["org_id"] == str(org)
    assert access.KONTRAKT_RED in me["permissions"]
    assert access.OKONOMI not in me["permissions"]


def test_login_wrong_password_and_unknown_email_look_the_same(client, make_org, make_user):
    org = make_org("A")
    make_user(org, "a@test.dk", "auditor", password=PW)
    r1 = client.post(
        "/api/auth/login", json={"email": "a@test.dk", "password": "forkert-adgangskode"}
    )
    r2 = client.post("/api/auth/login", json={"email": "findes-ikke@test.dk", "password": "x"})
    assert r1.status_code == r2.status_code == 401
    assert r1.json()["detail"]["code"] == r2.json()["detail"]["code"] == "bad_credentials"


def test_deactivated_user_cannot_login_and_existing_token_dies(
    client, make_org, make_user, Session_, migrator_engine
):
    org = make_org("A")
    uid = make_user(org, "u@test.dk", "auditor", password=PW)
    tok = _login(client, "u@test.dk")
    with migrator_engine.connect() as c:
        c.execute(text("UPDATE profiles SET deactivated_at = now() WHERE id = :i"), {"i": uid})
    assert (
        client.post("/api/auth/login", json={"email": "u@test.dk", "password": PW}).status_code
        == 401
    )
    r = client.get("/api/me", headers=_auth(tok))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "account_deactivated"


def test_no_token_and_bad_token(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers=_auth("not-a-token")).status_code == 401


def test_login_is_rate_limited(client, make_org, make_user, monkeypatch):
    from app.core.config import settings

    org = make_org("A")
    make_user(org, "r@test.dk", "auditor", password=PW)
    monkeypatch.setattr(settings, "ratelimit_login", "3 per minute")
    limiter.reset()
    try:
        codes = [
            client.post("/api/auth/login", json={"email": "r@test.dk", "password": "x"}).status_code
            for _ in range(4)
        ]
        assert codes == [401, 401, 401, 429]
    finally:
        limiter.reset()


# ---- RBAC + RLS through the API ----------------------------------------------------


def test_contracts_list_is_rls_scoped_over_http(client, make_org, make_user, make_contract):
    org_a, org_b = make_org("A"), make_org("B")
    make_user(org_a, "a@test.dk", "contract_manager", password=PW)
    make_user(org_b, "b@test.dk", "contract_manager", password=PW)
    make_contract(org_a, "K-2026-001")
    make_contract(org_b, "K-2026-002")
    ta, tb = _login(client, "a@test.dk"), _login(client, "b@test.dk")
    assert [
        c["reference"] for c in client.get("/api/contracts", headers=_auth(ta)).json()["items"]
    ] == ["K-2026-001"]
    assert [
        c["reference"] for c in client.get("/api/contracts", headers=_auth(tb)).json()["items"]
    ] == ["K-2026-002"]


def test_business_user_cannot_create_but_can_read(client, make_org, make_user):
    org = make_org("A")
    make_user(org, "bu@test.dk", "business_user", password=PW)
    tok = _login(client, "bu@test.dk")
    r = client.post(
        "/api/contracts", headers=_auth(tok), json={"reference": "K-2026-010", "name": "x"}
    )
    assert r.status_code == 403 and r.json()["detail"]["code"] == "forbidden"
    assert client.get("/api/contracts", headers=_auth(tok)).status_code == 200


def test_create_then_list_and_financials_masked_without_okonomi(client, make_org, make_user):
    org = make_org("A")
    make_user(org, "cm@test.dk", "contract_manager", password=PW)  # kontrakt_red, no okonomi
    make_user(org, "fc@test.dk", "finance_controller", password=PW)  # okonomi, no kontrakt_red
    make_user(org, "sa@test.dk", "systemadministrator", password=PW)  # both
    t_sa = _login(client, "sa@test.dk")
    r = client.post(
        "/api/contracts",
        headers=_auth(t_sa),
        json={"reference": "K-2026-020", "name": "IT-drift", "annual_value": "6125000.00"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["annual_value"] == "6125000.00"

    t_cm = _login(client, "cm@test.dk")
    row = client.get("/api/contracts", headers=_auth(t_cm)).json()["items"][0]
    assert row["reference"] == "K-2026-020" and row["annual_value"] is None  # masked, not 0

    t_fc = _login(client, "fc@test.dk")
    row = client.get("/api/contracts", headers=_auth(t_fc)).json()["items"][0]
    assert row["annual_value"] == "6125000.00"


def test_duplicate_reference_is_409(client, make_org, make_user):
    org = make_org("A")
    make_user(org, "sa@test.dk", "systemadministrator", password=PW)
    tok = _login(client, "sa@test.dk")
    body = {"reference": "K-2026-030", "name": "x"}
    assert client.post("/api/contracts", headers=_auth(tok), json=body).status_code == 201
    r = client.post("/api/contracts", headers=_auth(tok), json=body)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "reference_taken"


def test_creator_sees_own_fortrolig_contract(client, make_org, make_user):
    """Migration 0001 docstring: INSERT … RETURNING needs SELECT visibility; the
    API makes the creator manager when nobody else is named."""
    org = make_org("A")
    make_user(org, "pm@test.dk", "procurement_manager", password=PW)
    make_user(org, "bu@test.dk", "business_user", password=PW)
    t_pm = _login(client, "pm@test.dk")
    r = client.post(
        "/api/contracts",
        headers=_auth(t_pm),
        json={"reference": "R-2026-001", "name": "Fortrolig", "confidentiality": "fortrolig"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["manager_id"] is not None
    assert [
        c["reference"] for c in client.get("/api/contracts", headers=_auth(t_pm)).json()["items"]
    ] == ["R-2026-001"]
    t_bu = _login(client, "bu@test.dk")
    assert client.get("/api/contracts", headers=_auth(t_bu)).json()["items"] == []


# ---- ADR-0003: table and code never drift ----------------------------------------


def test_role_permissions_table_matches_code_matrix(Session_):
    with Session_() as s:
        rows = {(r.role.value, r.permission) for r in s.scalars(select(RolePermission))}
    assert rows == set(access.seed_rows())
