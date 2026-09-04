"""Obligation Extraction Agent + the register (ADR-0004 create, ADR-0005 citations
and re-resolution, ADR-0011 access events) over HTTP with a scripted provider."""

from __future__ import annotations

import json
import re

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.rls import tenant
from app.domain.models import AuditLog, Citation
from app.llm import provider as llm_provider
from app.llm.provider import FakeProvider, FakeResponse

pytestmark = pytest.mark.integration

PW = "korrekt-adgangskode-123"

PAGE1 = "SERVICEAFTALE om IT-drift\n1. Aftalens genstand\nLeverandøren varetager drift af logistiksystemet."
PAGE2 = (
    "5. Rapportering\n"
    "5.1 Leverandøren fremsender kvartalsvis driftsrapport senest den 31. januar 2026 for fjerde kvartal.\n"
    "8. Servicemål\n"
    "8.1 Leverandøren garanterer en oppetid på minimum 99,8 % pr. måned."
)
# v2: the reporting duty is negotiated away; the uptime clause moves to a new page.
PAGE2_V2 = "5. Rapportering\n5.1 Udgået."
PAGE3_V2 = "8. Servicemål\n8.1 Leverandøren garanterer en oppetid på minimum 99,8 % pr. måned."


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


def _intake_empty() -> str:
    none = {"value": None, "citation": None}
    return json.dumps(
        {
            k: none
            for k in (
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
        }
        | {"options": [], "confidence": "lav", "rationale": "ingen stamdata"}
    )


def _obligations(doc_id: str) -> str:
    def item(title, quote, page, conf="hoej", **extra):
        return {
            "title": title,
            "description": f"{title}.",
            "party": "leverandoer",
            "frequency": "kvartalsvis",
            "deadline": None,
            "criticality": "hoej",
            "consequence": None,
            "confidence": conf,
            "citation": {"document_id": doc_id, "page_pdf": page, "quote": quote},
            **extra,
        }

    return json.dumps(
        {
            "obligations": [
                item(
                    "Levere kvartalsvis driftsrapport",
                    "fremsender kvartalsvis driftsrapport senest den 31. januar 2026",
                    2,
                    deadline="2026-01-31",
                ),
                item(
                    "Opretholde oppetid på 99,8 %",
                    "garanterer en oppetid på minimum 99,8 % pr. måned",
                    2,
                    frequency="loebende",
                ),
                item(
                    "Betale bod ved forsinket levering",
                    "bod på 5 % af vederlaget pr. påbegyndt uge",
                    2,
                    conf="hoej",
                ),
            ],
            "rationale": "To pligter står ordret; boden er fortolket.",
        }
    )


class _Router(FakeProvider):
    """Answers by task: the same version switch triggers both agents."""

    reword = False  # a rerun phrases the same duties differently

    def complete(self, req):
        m = re.search(r'<dokument id="([^"]+)"', req.material)
        doc_id = m.group(1) if m else "?"
        if "Obligation Extraction Agent" in req.system:
            text = _obligations(doc_id)
            if self.reword:
                text = text.replace(
                    "Levere kvartalsvis driftsrapport", "Fremsende driftsrapport hvert kvartal"
                )
                text = text.replace("Opretholde oppetid på 99,8 %", "Garantere 99,8 % oppetid")
        else:
            text = _intake_empty()
        self.responses.append(FakeResponse(text))
        return super().complete(req)


@pytest.fixture
def router():
    p = _Router()
    llm_provider.set_provider(p)
    yield p
    llm_provider.set_provider(None)


@pytest.fixture
def cm(client, make_org, make_user, make_contract):
    org = make_org("A")
    make_user(org, "cm@test.dk", "contract_manager", password=PW)
    contract = make_contract(org, "K-2026-001")
    return org, contract, _login(client, "cm@test.dk")


def _upload(client, h, contract, data: bytes):
    return client.post(
        f"/api/contracts/{contract}/documents",
        headers=h,
        files={"file": ("k.pdf", data, "application/pdf")},
        data={"doc_type": "hovedkontrakt", "title": "Hovedkontrakt"},
    )


def _proposals(client, contract, h):
    rows = client.get(f"/api/contracts/{contract}/suggestions", headers=h).json()
    return [s for s in rows if s["subject_kind"] == "obligation"]


def _runs(client, contract, h, key="obligation_extract"):
    rows = client.get(f"/api/contracts/{contract}/agent-runs", headers=h).json()
    return [r for r in rows if r["agent_key"] == key]


# ---- extraction ------------------------------------------------------------------------------


def test_extract_proposes_one_create_suggestion_per_duty_with_verified_citations(
    client, cm, router
):
    _, contract, h = cm
    assert _upload(client, h, contract, _pdf(PAGE1, PAGE2)).status_code == 201
    (run,) = _runs(client, contract, h)
    assert run["status"] == "ok" and run["suggestions_created"] == 3, run
    props = sorted(_proposals(client, contract, h), key=lambda s: s["payload"]["title"])
    assert [p["kind"] for p in props] == ["create"] * 3
    by_title = {p["payload"]["title"]: p for p in props}
    rapport = by_title["Levere kvartalsvis driftsrapport"]
    assert rapport["confidence"] == "hoej"
    (cite,) = rapport["citations"]
    assert cite["verified"] and cite["page_pdf"] == 2 and cite["clause_ref"] == "5.1"
    assert cite["label"] == "Hovedkontrakt · s. 2 · pkt. 5.1"
    bod = by_title["Betale bod ved forsinket levering"]
    assert bod["confidence"] == "lav" and bod["citations"][0]["verified"] is False  # capped
    # the register is untouched until a human decides
    assert client.get(f"/api/contracts/{contract}/obligations", headers=h).json() == []


def test_rerun_updates_the_same_suggestions_even_when_reworded(client, cm, router):
    _, contract, h = cm
    _upload(client, h, contract, _pdf(PAGE1, PAGE2))
    router.reword = True
    assert (
        client.post(
            f"/api/contracts/{contract}/agents/obligation_extract/run", headers=h
        ).status_code
        == 202
    )
    manual = _runs(client, contract, h)[0]
    assert manual["trigger"] == "manual"
    assert manual["suggestions_created"] == 0 and manual["suggestions_updated"] == 3
    assert len(_proposals(client, contract, h)) == 3


# ---- the human's act -------------------------------------------------------------------------


def test_approve_materializes_obligation_with_citations_and_derived_status(
    client, cm, router, Session_
):
    org, contract, h = cm
    _upload(client, h, contract, _pdf(PAGE1, PAGE2))
    rapport = next(
        p for p in _proposals(client, contract, h) if p["payload"]["title"].startswith("Levere")
    )
    r = client.post(
        f"/api/suggestions/{rapport['id']}/approve", headers=h, json={"comment": "korrekt"}
    )
    assert r.status_code == 200 and r.json()["status"] == "godkendt", r.text

    (o,) = client.get(f"/api/contracts/{contract}/obligations", headers=h).json()
    assert o["ref"] == "F-1" and o["origin"] == "ai" and o["suggestion_id"] == rapport["id"]
    assert (
        o["party"] == "leverandoer"
        and o["frequency"] == "kvartalsvis"
        and o["deadline"] == "2026-01-31"
    )
    assert (
        o["status"] == "aaben" and o["effective_status"] == "forsinket"
    )  # derived: deadline passed
    (c,) = o["citations"]
    assert c["verified"] and c["clause_ref"] == "5.1" and c["quote"].startswith("fremsender")
    assert o["source_stale"] is False

    with tenant(org, system=True), Session_() as db:
        actions = [a.action.value for a in db.scalars(select(AuditLog))]
        assert "obligation_created" in actions and "ai_suggestion_approved" in actions
        (row,) = db.scalars(select(Citation)).all()
        assert row.subject_kind == "obligation" and row.quote_hash


def test_bulk_approve_takes_only_high_confidence(client, cm, router):
    _, contract, h = cm
    _upload(client, h, contract, _pdf(PAGE1, PAGE2))
    ids = [p["id"] for p in _proposals(client, contract, h)]
    r = client.post("/api/suggestions/bulk-approve", headers=h, json={"ids": ids})
    assert r.status_code == 200, r.text
    assert len(r.json()["approved"]) == 2
    (failed,) = r.json()["failed"]
    assert failed["code"] == "not_bulk_eligible"
    refs = sorted(
        o["ref"] for o in client.get(f"/api/contracts/{contract}/obligations", headers=h).json()
    )
    assert refs == ["F-1", "F-2"]
    # second bulk on the same ids: already decided / still ineligible, nothing new
    r = client.post("/api/suggestions/bulk-approve", headers=h, json={"ids": ids})
    assert r.json()["approved"] == [] and len(r.json()["failed"]) == 3


def test_business_user_cannot_decide_or_edit(client, cm, router, make_user):
    org, contract, h = cm
    _upload(client, h, contract, _pdf(PAGE1, PAGE2))
    (p, *_) = _proposals(client, contract, h)
    make_user(org, "bu@test.dk", "business_user", password=PW)
    h_bu = _login(client, "bu@test.dk")
    assert (
        client.post(
            "/api/suggestions/bulk-approve", headers=h_bu, json={"ids": [p["id"]]}
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/contracts/{contract}/obligations", headers=h_bu, json={"title": "x"}
        ).status_code
        == 403
    )
    assert client.get(f"/api/contracts/{contract}/obligations", headers=h_bu).status_code == 200


# ---- version switch: expiry + re-resolution (ADR-0005 §5) ------------------------------------


def test_new_version_expires_open_proposals_and_reresolves_register_citations(
    client, cm, router, Session_
):
    org, contract, h = cm
    doc = _upload(client, h, contract, _pdf(PAGE1, PAGE2)).json()
    props = _proposals(client, contract, h)
    # approve the two verified ones → register rows with citations on v1
    for p in props:
        if p["confidence"] == "hoej":
            assert (
                client.post(f"/api/suggestions/{p['id']}/approve", headers=h, json={}).status_code
                == 200
            )
    assert len(client.get(f"/api/contracts/{contract}/obligations", headers=h).json()) == 2

    v2 = client.post(
        f"/api/documents/{doc['id']}/versions",
        headers=h,
        files={"file": ("v2.pdf", _pdf(PAGE1, PAGE2_V2, PAGE3_V2), "application/pdf")},
    ).json()
    assert (
        client.post(f"/api/documents/versions/{v2['id']}/make-current", headers=h).status_code
        == 200
    )

    # the open (lav) proposal cited v1 → foraeldet; the agents ran again on v2
    statuses = sorted(s["status"] for s in _proposals(client, contract, h))
    assert "foraeldet" in statuses and statuses.count("godkendt") == 2
    assert _runs(client, contract, h)[0]["trigger"] == "event"

    by_ref = {
        o["ref"]: o for o in client.get(f"/api/contracts/{contract}/obligations", headers=h).json()
    }
    rapport = next(o for o in by_ref.values() if o["title"].startswith("Levere"))
    oppetid = next(o for o in by_ref.values() if o["title"].startswith("Opretholde"))
    assert rapport["source_stale"] is True
    assert rapport["citations"][0]["successor_status"] == "ikke_fundet"
    old, new = sorted(oppetid["citations"], key=lambda c: c["document_version_id"] == v2["id"])
    assert old["successor_status"] == "flyttet" and old["successor_id"] == new["id"]
    assert (
        new["document_version_id"] == v2["id"]
        and new["page_pdf"] == 3
        and new["clause_ref"] == "8.1"
    )
    assert oppetid["source_stale"] is False

    with tenant(org, system=True), Session_() as db:
        row = next(
            a for a in db.scalars(select(AuditLog)) if a.action.value == "citations_reresolved"
        )
        assert row.details == {
            "old_version_id": doc["versions"][0]["id"],
            "uaendret": 0,
            "flyttet": 1,
            "ikke_fundet": 1,
        }


# ---- manual register edits + access events ---------------------------------------------------


def test_manual_create_and_status_change_are_audited(client, cm, Session_):
    org, contract, h = cm
    r = client.post(
        f"/api/contracts/{contract}/obligations",
        headers=h,
        json={
            "title": "Afholde styregruppemøde",
            "party": "begge",
            "frequency": "kvartalsvis",
            "criticality": "lav",
        },
    )
    assert r.status_code == 201, r.text
    o = r.json()
    assert o["ref"] == "F-1" and o["origin"] == "human" and o["effective_status"] == "aaben"
    r = client.patch(
        f"/api/obligations/{o['id']}", headers=h, json={"status": "opfyldt", "note": "afholdt 1/9"}
    )
    assert r.status_code == 200 and r.json()["status"] == "opfyldt" and r.json()["fulfilled_at"]
    with tenant(org, system=True), Session_() as db:
        actions = [
            a.action.value for a in db.scalars(select(AuditLog).order_by(AuditLog.occurred_at))
        ]
        assert actions[-2:] == ["obligation_updated", "obligation_status_changed"]


def test_login_upload_and_version_switch_are_in_the_audit_log(client, cm, Session_):
    org, contract, h = cm
    assert (
        client.post(
            "/api/auth/login", json={"email": "cm@test.dk", "password": "forkert-x"}
        ).status_code
        == 401
    )
    _upload(client, h, contract, _pdf(PAGE1))
    with tenant(org, system=True), Session_() as db:
        rows = db.scalars(select(AuditLog).order_by(AuditLog.occurred_at)).all()
        actions = [a.action.value for a in rows]
        assert actions[0] == "login"  # the fixture's login
        assert "login_failed" in actions and "document_uploaded" in actions
        assert "document_version_made_current" in actions
        login = rows[0]
        assert login.actor_label == "cm" and login.actor_role == "contract_manager"
