"""RACI as data (ADR-0021): proposals from templates + clauses, validation on
approval and on edit, staffing mirrored to owner/manager, gap rules G1–G6 with
dedupe and auto-close, workload with a named candidate, tasks."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.core.rls import tenant
from app.domain.models import (
    Contract,
    ContractPhase,
    ContractRole,
    ContractStatus,
    ContractTier,
    Criticality,
    Obligation,
    ObligationFrequency,
    ObligationParty,
    Origin,
    RaciActivity,
    RaciAssignment,
    RaciFunction,
    RaciLetter,
)
from app.llm import provider as llm_provider
from app.llm.provider import FakeProvider, FakeResponse

pytestmark = pytest.mark.integration

PW = "korrekt-adgangskode-123"
AGREEMENT = (
    "RAMMEAFTALE om levering af lægemidler\n"
    "5. Levering\n"
    "5.1 Leverandøren opretholder en leveringsgrad på mindst 98,5 %.\n"
    "5.4 Kunden varsler leverandøren om patientkritiske uger senest 14 dage før."
)


def _login(client: TestClient, email: str) -> dict[str, str]:
    r = client.post("/api/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _pdf(*pages: str) -> bytes:
    doc = pymupdf.open()
    for body in pages:
        page = doc.new_page()
        y = 72
        for line in body.split("\n"):
            page.insert_text((50, y), line, fontsize=9)
            y += 14
    data: bytes = doc.tobytes()
    doc.close()
    return data


def _empty_intake() -> str:
    none = {"value": None, "citation": None}
    keys = (
        "name",
        "contract_number",
        "agreement_form",
        "category",
        "description",
        "start_date",
        "end_date",
        "notice_period_months",
        "last_termination_date",
        "price_regulation",
        "total_value_dkk",
        "annual_value_dkk",
    )
    return json.dumps(
        {k: none for k in keys} | {"options": [], "confidence": "lav", "rationale": "-"}
    )


def _raci(doc_id: str) -> str:
    return json.dumps(
        {
            "activities": [
                {
                    "name": "Følge op på leveringsgrad og SLA",
                    "criticality": "hoej",
                    "template_key": "sla_followup",
                    "cells": [
                        {"function": "CM", "letter": "A"},
                        {"function": "BUS", "letter": "R"},
                        {"function": "LEV", "letter": "I"},
                    ],
                    "confidence": "hoej",
                    "citation": None,
                },
                {
                    "name": "Varsle patientkritiske uger til leverandøren",
                    "criticality": "hoej",
                    "template_key": None,
                    "cells": [
                        {"function": "CM", "letter": "A"},
                        {"function": "BUS", "letter": "R"},
                        {"function": "LEV", "letter": "I"},
                    ],
                    "confidence": "hoej",
                    "citation": {
                        "document_id": doc_id,
                        "page_pdf": 1,
                        "quote": "varsler leverandøren om patientkritiske uger senest 14 dage før",
                    },
                },
                {
                    # the mockup's RA-6: two R, no A — invalid
                    "name": "Opfølgning på restordrer i patientkritiske uger",
                    "criticality": "hoej",
                    "template_key": None,
                    "cells": [
                        {"function": "CM", "letter": "R"},
                        {"function": "BUS", "letter": "R"},
                    ],
                    "confidence": "hoej",
                    "citation": None,
                },
            ],
            "rationale": "Skabelon for N1-rammeaftale plus varslingsklausulen i 5.4.",
        }
    )


class _Router(FakeProvider):
    def complete(self, req):
        m = re.search(r'<dokument id="([^"]+)"', req.material)
        doc_id = m.group(1) if m else "?"
        if "RACI Design Agent" in req.system:
            text_ = _raci(doc_id)
        elif "Obligation Extraction Agent" in req.system:
            text_ = json.dumps(
                {
                    "obligations": [],
                    "kpis": [],
                    "price_terms": [],
                    "penalty_terms": [],
                    "rationale": "-",
                }
            )
        elif "Risk Agent" in req.system:
            text_ = json.dumps({"risks": [], "rationale": "-"})
        else:
            text_ = _empty_intake()
        self.responses.append(FakeResponse(text_))
        return super().complete(req)


@pytest.fixture
def router():
    p = _Router()
    llm_provider.set_provider(p)
    yield p
    llm_provider.set_provider(None)


@pytest.fixture
def world(client, make_org, make_user, make_contract, Session_):
    org = make_org("A")
    cm = make_user(org, "cm@test.dk", "contract_manager", password=PW)  # raci_godkend + agenter
    co = make_user(org, "co@test.dk", "contract_owner", password=PW)
    fin = make_user(org, "fin@test.dk", "finance_controller", password=PW)
    make_user(org, "bu@test.dk", "business_user", password=PW)
    contract = make_contract(org, "K-2026-001", owner_id=co)
    with tenant(org, system=True), Session_() as db:
        c = db.get(Contract, contract)
        c.tier = ContractTier.N1
        c.status = ContractStatus.aktiv
        db.commit()
    h = _login(client, "cm@test.dk")
    # the fixture inserts the contract directly, so mirror owner → CO the way the API would
    assert (
        client.put(
            f"/api/contracts/{contract}/roles/CO", headers=h, json={"profile_id": str(co)}
        ).status_code
        == 200
    )
    return org, contract, cm, co, fin, h


def test_raci_proposals_from_templates_and_clauses_and_validation_on_approval(
    client, world, router
):
    _, contract, _, _, _, h = world
    r = client.post(
        f"/api/contracts/{contract}/documents",
        headers=h,
        files={"file": ("k.pdf", _pdf(AGREEMENT), "application/pdf")},
        data={"doc_type": "hovedkontrakt", "title": "Rammeaftale"},
    )
    assert r.status_code == 201
    # the agent saw the templates as a data block (N1 rammeaftale matches sla_followup)
    req = next(q for q in router.requests if "RACI Design Agent" in q.system)
    assert (
        "sla_followup" in req.material
        and "CM" not in req.system.split("Regler")[0].split("otte funktioner")[0]
    )
    props = [
        s
        for s in client.get(f"/api/contracts/{contract}/suggestions", headers=h).json()
        if s["subject_kind"] == "raci_entry"
    ]
    assert len(props) == 3
    by_name = {p["payload"]["name"]: p for p in props}
    ra6 = by_name["Opfølgning på restordrer i patientkritiske uger"]
    assert ra6["confidence"] == "lav" and "intet A" in ra6["payload"]["validation_errors"][0]
    varsle = by_name["Varsle patientkritiske uger til leverandøren"]
    assert varsle["citations"][0]["verified"] and varsle["citations"][0]["clause_ref"] == "5.4"
    # an invalid matrix cannot be approved as is …
    r = client.post(f"/api/suggestions/{ra6['id']}/approve", headers=h, json={})
    assert r.status_code == 409 and "ugyldig" in r.json()["detail"]["error"]
    # … but can be corrected by the human before approval (ADR-0021 §4)
    r = client.post(
        f"/api/suggestions/{ra6['id']}/approve",
        headers=h,
        json={"payload_overrides": {"cells": {"CO": "A", "CM": "R", "BUS": "R"}}},
    )
    assert r.status_code == 200, r.text
    assert "rettet før godkendelse: cells" in r.json()["decision_comment"]
    assert (
        client.post(f"/api/suggestions/{varsle['id']}/approve", headers=h, json={}).status_code
        == 200
    )
    raci = client.get(f"/api/contracts/{contract}/raci", headers=h).json()
    assert [a["ref"] for a in raci["activities"]] == ["RA-1", "RA-2"]
    assert (
        raci["activities"][0]["cells"] == {"CO": "A", "CM": "R", "BUS": "R"}
        and raci["activities"][0]["origin"] == "ai"
    )
    assert raci["activities"][1]["citations"][0]["clause_ref"] == "5.4"
    # the model never proposed a person
    assert all("profile_id" not in p["payload"] for p in props)


def test_cells_are_validated_on_edit_and_lev_never_accountable(client, world):
    _, contract, _, _, _, h = world
    a = client.post(
        f"/api/contracts/{contract}/raci/activities",
        headers=h,
        json={"name": "Fakturakontrol", "criticality": "mellem", "cells": {"FIN": "A", "CM": "R"}},
    ).json()
    assert a["ref"] == "RA-1" and a["validation_errors"] == []
    r = client.put(f"/api/raci/activities/{a['id']}/cells/CM", headers=h, json={"letter": None})
    assert r.status_code == 409 and "Mindst ét R" in r.json()["detail"]["error"]
    r = client.put(f"/api/raci/activities/{a['id']}/cells/LEV", headers=h, json={"letter": "A"})
    assert r.status_code == 409
    r = client.put(f"/api/raci/activities/{a['id']}/cells/LEV", headers=h, json={"letter": "I"})
    assert r.status_code == 200 and r.json()["cells"]["LEV"] == "I"
    assert (
        client.post(
            f"/api/contracts/{contract}/raci/activities",
            headers=h,
            json={"name": "x", "cells": {"CM": "A", "CO": "A", "BUS": "R"}},
        ).status_code
        == 409
    )
    h_bu = _login(client, "bu@test.dk")
    assert (
        client.put(
            f"/api/raci/activities/{a['id']}/cells/CM", headers=h_bu, json={"letter": "C"}
        ).status_code
        == 403
    )


def test_staffing_mirrors_owner_and_manager(client, world, Session_):
    org, contract, cm, co, fin, h = world
    # a contract created through the API with an owner gets its CO row at creation (§2)
    created = client.post(
        "/api/contracts",
        headers=h,
        json={"reference": "K-2026-777", "name": "Ny", "owner_id": str(co)},
    ).json()
    raci = client.get(f"/api/contracts/{created['id']}/raci", headers=h).json()
    assert {r["function"]: r for r in raci["roles"]}["CO"]["profile_id"] == str(co)
    raci = client.get(f"/api/contracts/{contract}/raci", headers=h).json()
    roles = {r["function"]: r for r in raci["roles"]}
    assert roles["CO"]["profile_id"] == str(co)
    r = client.put(f"/api/contracts/{contract}/roles/CM", headers=h, json={"profile_id": str(cm)})
    assert r.status_code == 200
    assert client.get(f"/api/contracts/{contract}", headers=h).json()["manager_id"] == str(cm)
    r = client.put(f"/api/contracts/{contract}/roles/LEV", headers=h, json={"profile_id": str(fin)})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "lev_is_not_a_user"
    assert (
        client.put(
            f"/api/contracts/{contract}/roles/LEV",
            headers=h,
            json={"supplier_contact": "Mette Leverandør"},
        ).status_code
        == 200
    )
    # replacing keeps history: the old row is closed, one active row remains
    client.put(f"/api/contracts/{contract}/roles/CM", headers=h, json={"profile_id": str(fin)})
    with tenant(org, system=True), Session_() as db:
        rows = db.scalars(
            select(ContractRole).where(
                ContractRole.contract_id == contract, ContractRole.function == RaciFunction.CM
            )
        ).all()
        assert len(rows) == 2 and sum(1 for x in rows if x.until is None) == 1


def test_gap_rules_find_close_and_dedupe(client, world, Session_):
    org, contract, cm, co, fin, h = world
    # G2: FIN is R on an activity, no FIN person; G6: aktiv_drift without CM
    client.post(
        f"/api/contracts/{contract}/raci/activities",
        headers=h,
        json={"name": "Fakturakontrol", "criticality": "hoej", "cells": {"CO": "A", "FIN": "R"}},
    )
    with tenant(org, system=True), Session_() as db:
        c = db.get(Contract, contract)
        c.phase = ContractPhase.aktiv_drift
        # G1: an approved activity without A (slipped through before validation existed)
        a = RaciActivity(
            organization_id=org,
            contract_id=contract,
            seq=9,
            name="RA uden A",
            criticality=Criticality.mellem,
            origin=Origin.human,
        )
        db.add(a)
        db.flush()
        db.add(
            RaciAssignment(
                organization_id=org,
                contract_id=contract,
                activity_id=a.id,
                function=RaciFunction.CM,
                letter=RaciLetter.R,
            )
        )
        # G4: obligation whose responsible is deactivated
        db.add(
            Obligation(
                organization_id=org,
                contract_id=contract,
                seq=1,
                title="Rapport",
                party=ObligationParty.leverandoer,
                frequency=ObligationFrequency.aarlig,
                criticality=Criticality.mellem,
                origin=Origin.human,
                responsible_id=fin,
            )
        )
        db.commit()
    with Session_() as db:
        db.execute(
            text("UPDATE profiles SET deactivated_at = :t WHERE id = :i"),
            {"t": datetime.now(UTC), "i": fin},
        )
        db.commit()
    assert client.post("/api/agents/responsibility_gap/run", headers=h).status_code == 202
    props = [
        s
        for s in client.get(f"/api/contracts/{contract}/suggestions", headers=h).json()
        if s["subject_kind"] == "task" and s["status"] == "foreslaaet"
    ]
    rules = sorted(p["payload"]["rule"] for p in props)
    assert rules == ["G1", "G2", "G2", "G4", "G6"], rules  # G2: FIN on RA-1, CM on the A-less row
    assert any(
        "Finance Controller er R" in p["payload"]["title"]
        for p in props
        if p["payload"]["rule"] == "G2"
    )
    # a second run on the same state: same proposals, nothing new
    client.post("/api/agents/responsibility_gap/run", headers=h)
    props2 = [
        s
        for s in client.get(f"/api/contracts/{contract}/suggestions", headers=h).json()
        if s["subject_kind"] == "task" and s["status"] == "foreslaaet"
    ]
    assert {p["id"] for p in props2} == {p["id"] for p in props}
    # close the G6 gap (staff CM) and G2 (staff FIN with the owner) → auto-closed next run
    client.put(f"/api/contracts/{contract}/roles/CM", headers=h, json={"profile_id": str(cm)})
    client.put(f"/api/contracts/{contract}/roles/FIN", headers=h, json={"profile_id": str(co)})
    client.post("/api/agents/responsibility_gap/run", headers=h)
    after = {
        s["payload"]["rule"]: s["status"]
        for s in client.get(f"/api/contracts/{contract}/suggestions", headers=h).json()
        if s["subject_kind"] == "task"
    }
    assert (
        after["G6"] == "foraeldet" and after["G2"] == "foraeldet" and after["G1"] == "foreslaaet"
    )  # both G2 closed
    # approving a gap finding creates a task with the finding as origin
    g4 = next(p for p in props if p["payload"]["rule"] == "G4")
    r = client.post(f"/api/suggestions/{g4['id']}/approve", headers=h, json={})
    assert r.status_code == 200
    (task,) = client.get(f"/api/contracts/{contract}/tasks", headers=h).json()
    assert task["ref"] == "T-1" and task["origin_kind"] == "gap:G4" and task["origin"] == "ai"
    r = client.patch(f"/api/tasks/{task['id']}", headers=h, json={"status": "lukket"})
    assert r.json()["closed_at"] is not None


def test_g3_deactivated_holder_gets_a_candidate(client, world, make_contract, Session_):
    org, contract, cm, co, fin, h = world
    other = make_contract(org, "K-2026-002")
    client.put(f"/api/contracts/{contract}/roles/CM", headers=h, json={"profile_id": str(fin)})
    client.put(f"/api/contracts/{other}/roles/CM", headers=h, json={"profile_id": str(cm)})
    with Session_() as db:
        db.execute(
            text("UPDATE profiles SET deactivated_at = :t WHERE id = :i"),
            {"t": datetime.now(UTC), "i": fin},
        )
        db.commit()
    client.post("/api/agents/responsibility_gap/run", headers=h)
    g3 = next(
        s
        for s in client.get(f"/api/contracts/{contract}/suggestions", headers=h).json()
        if s["payload"].get("rule") == "G3"
    )
    assert "fratrådt" in g3["payload"]["title"] and g3["payload"]["candidate_id"] == str(cm)
    assert "Foreslået: cm" in g3["payload"]["description"]
    r = client.post(f"/api/suggestions/{g3['id']}/approve", headers=h, json={})
    assert r.status_code == 200
    (task,) = client.get(f"/api/contracts/{contract}/tasks", headers=h).json()
    assert task["responsible_id"] == str(cm)  # the candidate becomes responsible for fixing it


def test_workload_threshold_names_a_candidate(client, world, make_contract, Session_):
    org, contract, cm, co, fin, h = world
    for i in range(2, 18):  # 17 contracts as CM for cm (> 15), fin holds one
        c = make_contract(org, f"K-2026-{i:03d}")
        client.put(f"/api/contracts/{c}/roles/CM", headers=h, json={"profile_id": str(cm)})
    client.put(f"/api/contracts/{contract}/roles/CM", headers=h, json={"profile_id": str(fin)})
    h_sa = h  # contract_manager lacks `brugere`; use a systemadministrator for /workload
    assert client.get("/api/workload", headers=h_sa).status_code == 403
    client.post("/api/agents/workload_capacity/run", headers=h)
    props = []
    for cid in client.get("/api/contracts", headers=h).json()["items"]:
        props += [
            s
            for s in client.get(f"/api/contracts/{cid['id']}/suggestions", headers=h).json()
            if s["payload"].get("rule") == "W1"
        ]
    assert len(props) == 1 and "cm er over belastningsgrænsen" in props[0]["payload"]["title"]
    assert props[0]["payload"]["candidate_name"] == "fin"
