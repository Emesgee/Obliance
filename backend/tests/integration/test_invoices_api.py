"""The invoice feed over HTTP (ADR-0018): import idempotence, three-step matching,
the price check in code (ADR-0013), findings → claims, human decisions, masking."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.rls import tenant
from app.domain.models import AuditLog, Contract, ContractStatus

pytestmark = pytest.mark.integration

PW = "korrekt-adgangskode-123"
HEAD = "fakturanr;fakturadato;forfaldsdato;leverandoer_cvr;kontraktreference;linje;beskrivelse;antal;enhed;enhedspris;linjetotal\n"


def _login(client: TestClient, email: str) -> dict[str, str]:
    r = client.post("/api/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _import(client, h, csv: str, name="fakturaer.csv"):
    return client.post(
        "/api/invoices/import", headers=h, files={"file": (name, csv.encode("utf-8"), "text/csv")}
    )


@pytest.fixture
def world(client, make_org, make_user, make_contract, Session_):
    org = make_org("A")
    make_user(org, "fc@test.dk", "finance_controller", password=PW)  # okonomi
    make_user(org, "cm@test.dk", "contract_manager", password=PW)  # kontrakt_red, no okonomi
    make_user(org, "bu@test.dk", "business_user", password=PW)
    h_fc, h_cm = _login(client, "fc@test.dk"), _login(client, "cm@test.dk")
    sup = client.post(
        "/api/suppliers", headers=h_fc, json={"cvr": "12345678", "name": "Nordisk IT-Drift A/S"}
    ).json()
    c1 = make_contract(org, "K-2026-001")
    c2 = make_contract(org, "K-2026-002")
    today = date.today()
    with tenant(org, system=True), Session_() as db:
        for cid in (c1, c2):
            c = db.get(Contract, cid)
            c.status = ContractStatus.aktiv
            c.supplier_id = sup["id"]
            c.start_date = today - timedelta(days=400)
            c.end_date = today + timedelta(days=400)
        db.commit()
    # agreed prices on K-2026-001 (the mockup's cases)
    for body in (
        {"description": "Farmaceuttimer, dagtimer", "unit": "time", "agreed_unit_price": "545.00"},
        {
            "product_ref": "AIP-771",
            "description": "Pakning A",
            "unit": "pakning",
            "agreed_unit_price": "1184.00",
        },
    ):
        assert (
            client.post(f"/api/contracts/{c1}/price-terms", headers=h_fc, json=body).status_code
            == 201
        )
    return org, c1, c2, sup, h_fc, h_cm


def test_import_matches_by_reference_and_checks_lines_in_code(client, world, Session_):
    org, c1, _, _, h_fc, h_cm = world
    csv = HEAD + (
        "10493;05-08-2026;04-09-2026;12345678;K-2026-001;1;Farmaceuttimer, dagtimer;37;time;590,00;21.830,00\n"
        "10493;05-08-2026;04-09-2026;12345678;K-2026-001;2;Kørsel;1;stk;1.250,50;1.250,50\n"
    )
    r = _import(client, h_fc, csv)
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["new"] == 1 and rep["matched"] == 1 and rep["rejected"] == 0
    (inv,) = client.get(f"/api/contracts/{c1}/invoices", headers=h_fc).json()
    assert inv["status"] == "kontrolleret" and inv["matched_by"] == "reference"
    assert (
        inv["control_result"] == "afvigelse"
        and "1 linje(r) over aftalt pris" in inv["control_note"]
    )
    assert "1 uden prisgrundlag" in inv["control_note"]  # "Kørsel" has no agreed price
    assert Decimal(inv["total_amount"]) == Decimal("23080.50") and len(inv["lines"]) == 2

    # the finding is a proposal for okonomi with the deviation computed in code
    sugg = [
        s
        for s in client.get(f"/api/contracts/{c1}/suggestions", headers=h_fc).json()
        if s["subject_kind"] == "invoice_finding"
    ]
    assert len(sugg) == 1
    f = sugg[0]
    assert f["payload"]["amount"] == "1665.00"  # (590 − 545) × 37
    assert f["payload"]["recommendation"].startswith("Afvis differencen")
    assert f["citations"][0]["kind"] == "record" and "linje 1" in f["citations"][0]["label"]
    assert (
        client.post(f"/api/suggestions/{f['id']}/approve", headers=h_cm, json={}).status_code == 403
    )
    r = client.post(f"/api/suggestions/{f['id']}/approve", headers=h_fc, json={})
    assert (
        r.status_code == 200 and "krav KR-1 beregnet (1665.00 kr.)" in r.json()["decision_comment"]
    )
    (claim,) = client.get(f"/api/contracts/{c1}/claims", headers=h_fc).json()
    assert claim["claim_type"] == "prisafvigelse" and claim["amount"] == "1665.00"
    assert claim["basis_text"].startswith(
        "(590,00 kr. − 545,00 kr.) pr. enhed × 37 enheder = 1.665,00 kr. — Faktura 10493 · linje 1"
    )

    # re-importing the same file changes nothing
    rep = _import(client, h_fc, csv).json()
    assert rep["new"] == 0 and rep["updated"] == 1
    assert len(client.get(f"/api/contracts/{c1}/invoices", headers=h_fc).json()) == 1

    with tenant(org, system=True), Session_() as db:
        actions = [a.action.value for a in db.scalars(select(AuditLog))]
        assert actions.count("invoices_imported") == 2
        assert "invoice_matched" in actions and "invoice_checked" in actions


def test_mockup_aip_case_by_product_ref(client, world):
    _, c1, _, _, h_fc, _ = world
    csv = (
        HEAD.replace("linjetotal", "linjetotal;produktref")
        + "20001;10-08-2026;;12345678;K-2026-001;1;Pakning A (AIP);3496;pakning;1.211,60;;AIP-771\n"
    )
    assert _import(client, h_fc, csv).status_code == 200
    (f,) = [
        s
        for s in client.get(f"/api/contracts/{c1}/suggestions", headers=h_fc).json()
        if s["subject_kind"] == "invoice_finding"
    ]
    assert f["payload"]["amount"] == "96489.60" and f["payload"]["agreed_unit_price"] == "1184.0000"


def test_corrected_invoice_supersedes_and_passes_control(client, world):
    _, c1, _, _, h_fc, _ = world
    csv1 = (
        HEAD
        + "10500;05-08-2026;;12345678;K-2026-001;1;Farmaceuttimer, dagtimer;10;time;590,00;5.900,00\n"
    )
    csv2 = (
        HEAD
        + "10500;05-08-2026;;12345678;K-2026-001;1;Farmaceuttimer, dagtimer;10;time;545,00;5.450,00\n"
    )
    _import(client, h_fc, csv1)
    rep = _import(client, h_fc, csv2).json()
    assert rep["new"] == 1 and rep["superseded"] == 1
    rows = client.get(f"/api/contracts/{c1}/invoices", headers=h_fc).json()
    by_total = {r["total_amount"]: r for r in rows}
    assert by_total["5900.00"]["status"] == "erstattet"
    new = by_total["5450.00"]
    assert new["supersedes_invoice_id"] == by_total["5900.00"]["id"]
    assert new["control_result"] == "bestaaet" and new["control_note"].startswith(
        "Kontrol bestået — klar til godkendelse"
    )
    r = client.post(f"/api/invoices/{new['id']}/approve", headers=h_fc, json={"comment": "ok"})
    assert r.status_code == 200 and r.json()["status"] == "godkendt"
    assert client.get(f"/api/contracts/{c1}/spend", headers=h_fc).json()["by_year"] == {
        "2026": "5450.00"
    }
    assert (
        client.post(f"/api/invoices/{new['id']}/approve", headers=h_fc, json={}).status_code == 409
    )


def test_ambiguous_supplier_goes_to_the_match_queue(client, world):
    _, c1, c2, _, h_fc, _ = world
    csv = HEAD + "30001;05-08-2026;;12345678;;1;Konsulenttimer;5;time;1.000,00;5.000,00\n"
    rep = _import(client, h_fc, csv).json()
    assert rep["queued"] == 1 and rep["matched"] == 0
    (q,) = client.get("/api/invoices?queue=unmatched", headers=h_fc).json()
    assert q["status"] == "modtaget" and q["contract_id"] is None
    assert {c["reference"] for c in q["candidates"]} == {"K-2026-001", "K-2026-002"}
    r = client.post(f"/api/invoices/{q['id']}/match", headers=h_fc, json={"contract_id": str(c2)})
    assert r.status_code == 200 and r.json()["matched_by"] == "manual"
    assert r.json()["control_result"] == "ingen_prisgrundlag"  # K-2026-002 has no price terms
    assert client.get("/api/invoices?queue=unmatched", headers=h_fc).json() == []
    # the match proposal was closed by the choice
    sugg = [
        s
        for s in client.get(f"/api/contracts/{c1}/suggestions", headers=h_fc).json()
        if s["subject_kind"] == "invoice_match"
    ]
    assert sugg and sugg[0]["status"] == "godkendt"


def test_single_active_contract_matches_by_rule(client, world, Session_):
    org, c1, c2, _, h_fc, _ = world
    with tenant(org, system=True), Session_() as db:
        db.get(Contract, c2).status = ContractStatus.udloebet
        db.commit()
    csv = HEAD + "30002;05-08-2026;;12345678;;1;Konsulenttimer;5;time;1.000,00;5.000,00\n"
    _import(client, h_fc, csv)
    (inv,) = client.get(f"/api/contracts/{c1}/invoices", headers=h_fc).json()
    assert inv["matched_by"] == "rule"


def test_unknown_cvr_is_an_error_row_and_creates_no_supplier(client, world):
    _, _, _, _, h_fc, _ = world
    csv = HEAD + "40001;05-08-2026;;99999999;K-2026-001;1;Noget;1;stk;10,00;10,00\n"
    rep = _import(client, h_fc, csv).json()
    assert (
        rep["new"] == 0
        and rep["rejected"] == 1
        and "ukendt leverandør-CVR 99999999" in rep["errors"][0]["reason"]
    )
    (err,) = client.get("/api/import-errors", headers=h_fc).json()
    assert err["row_no"] == 2 and err["raw"]["cvr"] == "99999999"
    assert [s["cvr"] for s in client.get("/api/suppliers", headers=h_fc).json()] == ["12345678"]


def test_permissions_and_masking(client, world):
    _, c1, _, _, h_fc, h_cm = world
    h_bu = _login(client, "bu@test.dk")
    csv = HEAD + "50001;05-08-2026;;12345678;K-2026-001;1;Kørsel;1;stk;10,00;10,00\n"
    assert _import(client, h_cm, csv).status_code == 403  # import needs okonomi
    assert _import(client, h_fc, csv).status_code == 200
    assert client.get("/api/invoices", headers=h_cm).status_code == 403
    assert client.get(f"/api/contracts/{c1}/invoices", headers=h_bu).status_code == 403
    assert client.get(f"/api/contracts/{c1}/invoices", headers=h_cm).status_code == 200
    (inv,) = client.get(f"/api/contracts/{c1}/invoices", headers=h_fc).json()
    r = client.post(f"/api/invoices/{inv['id']}/reject", headers=h_fc, json={"comment": ""})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "comment_required"
    r = client.post(
        f"/api/invoices/{inv['id']}/reject", headers=h_fc, json={"comment": "forkert kontrakt"}
    )
    assert r.json()["status"] == "afvist"
    d = client.get("/api/dashboard", headers=h_fc).json()["counts"]
    assert d["invoices_pending"] == 0 and d["invoices_unmatched"] == 0
