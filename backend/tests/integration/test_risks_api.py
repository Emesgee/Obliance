"""Risk Agent + the risks register: proposals with citations, derived score/level,
human verdicts, and G-07 (the worker role cannot write any register table)."""

from __future__ import annotations

import json
import re

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.core.rls import tenant
from app.domain.models import AuditLog
from app.llm import provider as llm_provider
from app.llm.provider import FakeProvider, FakeResponse

pytestmark = pytest.mark.integration

PW = "korrekt-adgangskode-123"

PAGE1 = (
    "SERVICEAFTALE om IT-drift\n"
    "3. Vederlag\n"
    "3.3 Vederlaget reguleres årligt efter leverandørens til enhver tid gældende prisliste.\n"
    "9. Databehandling\n"
    "9.1 Leverandøren kan anvende underdatabehandlere uden forudgående orientering af kunden."
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


def _intake_empty() -> str:
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


def _risks(doc_id: str) -> str:
    def item(title, quote, cat, p, k, conf="hoej"):
        return {
            "title": title,
            "description": f"{title}.",
            "category": cat,
            "probability": p,
            "consequence": k,
            "mitigation": "Forhandl et loft ind ved næste tillæg.",
            "confidence": conf,
            "citation": {"document_id": doc_id, "page_pdf": 1, "quote": quote},
        }

    return json.dumps(
        {
            "risks": [
                item(
                    "Ensidig prisregulering uden loft",
                    "reguleres årligt efter leverandørens til enhver tid gældende prisliste",
                    "kommerciel",
                    4,
                    4,
                ),
                item(
                    "Underdatabehandlere uden orientering",
                    "anvende underdatabehandlere uden forudgående orientering",
                    "gdpr",
                    3,
                    5,
                ),
                item(
                    "Manglende exit-bistand",
                    "leverandøren yder ingen bistand ved ophør",
                    "operationel",
                    2,
                    3,
                    conf="mellem",
                ),
            ],
            "rationale": "Prisklausulen og databehandlingen vejer tungest.",
        }
    )


class _Router(FakeProvider):
    def complete(self, req):
        m = re.search(r'<dokument id="([^"]+)"', req.material)
        doc_id = m.group(1) if m else "?"
        if "RACI Design Agent" in req.system:
            text = json.dumps({"activities": [], "rationale": "-"})
        elif "Risk Agent" in req.system:
            text = _risks(doc_id)
        elif "Obligation Extraction Agent" in req.system:
            text = json.dumps({"obligations": [], "rationale": "ingen"})
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


def _upload(client, h, contract):
    return client.post(
        f"/api/contracts/{contract}/documents",
        headers=h,
        files={"file": ("k.pdf", _pdf(PAGE1), "application/pdf")},
        data={"doc_type": "hovedkontrakt", "title": "Hovedkontrakt"},
    )


def _proposals(client, contract, h):
    rows = client.get(f"/api/contracts/{contract}/suggestions", headers=h).json()
    return [s for s in rows if s["subject_kind"] == "risk"]


def test_risk_agent_proposes_with_citations_and_caps_unverified(client, cm, router):
    _, contract, h = cm
    assert _upload(client, h, contract).status_code == 201
    runs = [
        r
        for r in client.get(f"/api/contracts/{contract}/agent-runs", headers=h).json()
        if r["agent_key"] == "risk_assess"
    ]
    assert runs and runs[0]["status"] == "ok" and runs[0]["suggestions_created"] == 3, runs
    props = {p["payload"]["title"]: p for p in _proposals(client, contract, h)}
    pris = props["Ensidig prisregulering uden loft"]
    assert pris["kind"] == "create" and pris["confidence"] == "hoej"
    assert pris["citations"][0]["verified"] and pris["citations"][0]["clause_ref"] == "3.3"
    assert pris["payload"]["probability"] == 4 and pris["payload"]["consequence"] == 4
    exit_ = props["Manglende exit-bistand"]
    assert exit_["confidence"] == "lav" and exit_["citations"][0]["verified"] is False
    assert client.get(f"/api/contracts/{contract}/risks", headers=h).json() == []


def test_approve_materializes_risk_with_derived_score_and_level(client, cm, router, Session_):
    org, contract, h = cm
    _upload(client, h, contract)
    props = _proposals(client, contract, h)
    ids = [p["id"] for p in props]
    r = client.post("/api/suggestions/bulk-approve", headers=h, json={"ids": ids})
    assert len(r.json()["approved"]) == 2 and r.json()["failed"][0]["code"] == "not_bulk_eligible"
    rows = {x["title"]: x for x in client.get(f"/api/contracts/{contract}/risks", headers=h).json()}
    gdpr = rows["Underdatabehandlere uden orientering"]
    assert gdpr["ref"] in ("R-1", "R-2") and gdpr["origin"] == "ai"
    assert gdpr["score"] == 15 and gdpr["level"] == "hoej"  # 3 × 5, derived
    assert gdpr["category"] == "gdpr" and gdpr["status"] == "aaben"
    (c,) = gdpr["citations"]
    assert c["verified"] and c["clause_ref"] == "9.1"
    with tenant(org, system=True), Session_() as db:
        actions = [a.action.value for a in db.scalars(select(AuditLog))]
        assert actions.count("risk_created") == 2


def test_manual_risk_and_status_change(client, cm, make_user):
    org, contract, h = cm
    r = client.post(
        f"/api/contracts/{contract}/risks",
        headers=h,
        json={
            "title": "Nøglepersonafhængighed",
            "category": "operationel",
            "probability": 2,
            "consequence": 2,
        },
    )
    assert r.status_code == 201 and r.json()["ref"] == "R-1" and r.json()["level"] == "lav"
    rid = r.json()["id"]
    r = client.patch(
        f"/api/risks/{rid}", headers=h, json={"status": "under_haandtering", "probability": 5}
    )
    assert (
        r.status_code == 200
        and r.json()["status"] == "under_haandtering"
        and r.json()["level"] == "mellem"
    )
    r = client.patch(f"/api/risks/{rid}", headers=h, json={"status": "lukket"})
    assert r.json()["closed_at"] is not None
    assert (
        client.post(
            f"/api/contracts/{contract}/risks", headers=h, json={"title": "x", "probability": 9}
        ).status_code
        == 422
    )
    make_user(org, "bu@test.dk", "business_user", password=PW)
    h_bu = _login(client, "bu@test.dk")
    assert (
        client.patch(f"/api/risks/{rid}", headers=h_bu, json={"status": "aaben"}).status_code == 403
    )
    assert client.get(f"/api/contracts/{contract}/risks", headers=h_bu).status_code == 200


def test_g07_worker_role_cannot_write_any_register_table(migrator_engine, migrated_schema):
    """ADR-0023 §5 G-07 / ADR-0004: agents write proposals only — as a grant."""
    with migrator_engine.connect() as c:
        for table in ("contracts", "obligations", "risks", "citations"):
            for priv in ("INSERT", "UPDATE", "DELETE"):
                ok = c.execute(
                    text("SELECT has_table_privilege('obliance_worker', :t, :p)"),
                    {"t": table, "p": priv},
                ).scalar()
                assert ok is False, f"worker may {priv} on {table}"
        for table in ("ai_suggestions", "agent_runs"):
            assert (
                c.execute(
                    text("SELECT has_table_privilege('obliance_worker', :t, 'INSERT')"),
                    {"t": table},
                ).scalar()
                is True
            )
