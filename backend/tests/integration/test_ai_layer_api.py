"""ADR-0004/0005/0008/0010/0011/0014 over HTTP with a scripted provider:
upload → intake run → suggestion with verified citations → human verdict."""

from __future__ import annotations

import json
import uuid

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.core.rls import tenant
from app.domain.models import AgentSetting, AuditLog, UsageEvent
from app.llm import provider as llm_provider
from app.llm.provider import FakeProvider, FakeResponse

pytestmark = pytest.mark.integration

PW = "korrekt-adgangskode-123"

PAGE1 = (
    "SERVICEAFTALE om IT-drift\n"
    "Kontraktnr. AMG-2026-017\n"
    "1. Ikrafttræden\n"
    "Aftalen træder i kraft den 1. januar 2027 og udløber den 31. december 2030."
)
PAGE2 = (
    "14. Opsigelse\n"
    "14.1 Kunden kan opsige aftalen med 6 måneders varsel.\n"
    "14.3 Kunden kan forlænge aftalen med op til 2 x 12 måneder."
)


def _login(client: TestClient, email: str) -> dict[str, str]:
    r = client.post("/api/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _pdf(*pages: str) -> bytes:
    doc = pymupdf.open()
    for body in pages:
        doc.new_page().insert_text((72, 72), body, fontsize=11)
    data: bytes = doc.tobytes()
    doc.close()
    return data


def _intake_json(doc_id: str, *, bad_quote: bool = False) -> str:
    def f(value, quote, page):
        return {
            "value": value,
            "citation": {"document_id": doc_id, "page_pdf": page, "quote": quote},
        }

    none = {"value": None, "citation": None}
    return json.dumps(
        {
            "name": f("Serviceaftale om IT-drift", "SERVICEAFTALE om IT-drift", 1),
            "contract_number": f("AMG-2026-017", "Kontraktnr. AMG-2026-017", 1),
            "agreement_form": f("serviceaftale", "SERVICEAFTALE om IT-drift", 1),
            "category": none,
            "description": none,
            "start_date": f("2027-01-01", "træder i kraft den 1. januar 2027", 1),
            "end_date": f(
                "2030-12-31",
                "Bod på 10 % hvis ikke leveret" if bad_quote else "udløber den 31. december 2030",
                1,
            ),
            "notice_period_months": f("6", "opsige aftalen med 6 måneders varsel", 2),
            "last_termination_date": none,
            "options": [
                {
                    "description": "Forlængelse 2 x 12 måneder",
                    "months": 24,
                    "citation": {
                        "document_id": doc_id,
                        "page_pdf": 2,
                        "quote": "forlænge aftalen med op til 2 x 12 måneder",
                    },
                }
            ],
            "price_regulation": none,
            "total_value_dkk": f("24500000.00", "SERVICEAFTALE om IT-drift", 1),
            "annual_value_dkk": none,
            "confidence": "hoej",
            "rationale": "Datoer og varsel står eksplicit.",
        }
    )


class _ScriptedProvider(FakeProvider):
    """Learns the document id from the material block, so the script can cite it."""

    def __init__(self, *, bad_quote: bool = False, responses=()):
        super().__init__(responses)
        self.bad_quote = bad_quote

    def complete(self, req):
        if not self.responses:
            import re

            m = re.search(r'<dokument id="([^"]+)"', req.material)
            self.responses.append(FakeResponse(_intake_json(m.group(1), bad_quote=self.bad_quote)))
        return super().complete(req)


@pytest.fixture
def scripted():
    p = _ScriptedProvider()
    llm_provider.set_provider(p)
    yield p
    llm_provider.set_provider(None)


@pytest.fixture
def cm(client, make_org, make_user, make_contract, Session_):
    org = make_org("A")
    make_user(org, "cm@test.dk", "contract_manager", password=PW)
    contract = make_contract(org, "K-2026-001")
    # This module tests the intake agent alone; the obligation agent (same trigger)
    # is paused for the org, which is also a test of ADR-0010 §2's switch.
    with tenant(org, system=True), Session_() as db:
        for key in ("obligation_extract", "risk_assess"):
            db.add(AgentSetting(organization_id=org, agent_key=key, enabled=False))
        db.commit()
    return org, contract, _login(client, "cm@test.dk")


def _runs(client, contract, h):
    rows = client.get(f"/api/contracts/{contract}/agent-runs", headers=h).json()
    return [r for r in rows if r["agent_key"] == "contract_intake"]


def _upload(client, h, contract, data: bytes, doc_type="hovedkontrakt"):
    return client.post(
        f"/api/contracts/{contract}/documents",
        headers=h,
        files={"file": ("k.pdf", data, "application/pdf")},
        data={"doc_type": doc_type, "title": "Hovedkontrakt"},
    )


# ---- the happy path: upload → run → suggestion → approve --------------------------------------


def test_upload_triggers_intake_and_creates_a_verified_suggestion(client, cm, scripted, Session_):
    org, contract, h = cm
    assert _upload(client, h, contract, _pdf(PAGE1, PAGE2)).status_code == 201

    # the provider saw the document as material, never in the instructions (ADR-0016 §1)
    (req,) = scripted.requests
    assert "SERVICEAFTALE om IT-drift" in req.material
    assert "SERVICEAFTALE" not in req.system
    assert req.output_schema is not None and "rationale" in req.output_schema["properties"]

    runs = _runs(client, contract, h)
    assert [r["status"] for r in runs] == ["ok"]
    assert runs[0]["trigger"] == "event" and runs[0]["suggestions_created"] == 1
    assert "model" not in runs[0]  # provenance is developer-only (ADR-0008)

    (s,) = client.get(f"/api/contracts/{contract}/suggestions", headers=h).json()
    assert s["status"] == "foreslaaet" and s["subject_kind"] == "contract_intake"
    assert s["confidence"] == "hoej"  # all citations located → not capped
    fields = s["payload"]["fields"]
    assert fields["start_date"]["value"] == "2027-01-01"
    cite = fields["start_date"]["citation"]
    assert cite["verified"] and cite["page_pdf"] == 1 and cite["clause_ref"] == "1"
    assert cite["label"].startswith("Hovedkontrakt · s. 1")
    assert fields["notice_period_months"]["citation"]["page_pdf"] == 2
    assert fields["notice_period_months"]["citation"]["clause_ref"] == "14.1"

    # measured (ADR-0014) and audited (ADR-0011) — once each
    with tenant(org, system=True), Session_() as db:
        (u,) = db.scalars(select(UsageEvent)).all()
        assert u.task == "contract_intake" and u.actor_type.value == "agent"
        assert u.cost_usd is None  # fake model has no price → row still written
        actions = [
            a.action.value for a in db.scalars(select(AuditLog).order_by(AuditLog.occurred_at))
        ]
        assert actions.count("ai_query") == 1
        assert "ai_suggestion_created" in actions and "agent_run_completed" in actions

    # approve → fill-only merge, kladde → aktiv, two audit rows
    r = client.post(f"/api/suggestions/{s['id']}/approve", headers=h, json={"comment": "ok"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "godkendt"
    c = client.get(f"/api/contracts/{contract}", headers=h).json()
    assert c["status"] == "aktiv"
    assert c["start_date"] == "2027-01-01" and c["end_date"] == "2030-12-31"
    assert c["contract_number"] == "AMG-2026-017" and c["agreement_form"] == "serviceaftale"
    assert c["notice_period_days"] == 180
    assert c["options"] == [{"beskrivelse": "Forlængelse 2 x 12 måneder", "maaneder": 24}]
    assert c["name"] == "Kontrakt K-2026-001"  # human-filled field wins (fill-only)
    assert c["total_value"] is None  # contract_manager lacks okonomi → not applied, not shown
    assert "delvist anvendt" in r.json()["decision_comment"]

    assert client.get(f"/api/contracts/{contract}/audit", headers=h).status_code == 200
    with tenant(org, system=True), Session_() as db:
        actions = [a.action.value for a in db.scalars(select(AuditLog))]
        assert "ai_suggestion_approved" in actions
        assert "contract_updated" in actions and "contract_status_changed" in actions


def test_unverified_citation_caps_confidence(client, cm, Session_):
    _, contract, h = cm
    p = _ScriptedProvider(bad_quote=True)
    llm_provider.set_provider(p)
    try:
        assert _upload(client, h, contract, _pdf(PAGE1, PAGE2)).status_code == 201
    finally:
        llm_provider.set_provider(None)
    (s,) = client.get(f"/api/contracts/{contract}/suggestions", headers=h).json()
    assert s["confidence"] == "lav"
    assert s["payload"]["fields"]["end_date"]["citation"]["verified"] is False


# ---- verdicts and permissions ----------------------------------------------------------------


def test_reject_requires_reason_and_business_user_cannot_decide(client, cm, scripted, make_user):
    org, contract, h = cm
    _upload(client, h, contract, _pdf(PAGE1, PAGE2))
    (s,) = client.get(f"/api/contracts/{contract}/suggestions", headers=h).json()

    make_user(org, "bu@test.dk", "business_user", password=PW)
    h_bu = _login(client, "bu@test.dk")
    assert (
        client.post(f"/api/suggestions/{s['id']}/approve", headers=h_bu, json={}).status_code == 403
    )

    r = client.post(f"/api/suggestions/{s['id']}/reject", headers=h, json={"comment": ""})
    assert r.status_code == 422  # schema: min_length
    r = client.post(
        f"/api/suggestions/{s['id']}/reject", headers=h, json={"comment": "Forkert dato"}
    )
    assert r.status_code == 200 and r.json()["status"] == "afvist"
    # decided → 409 on a second verdict
    r = client.post(f"/api/suggestions/{s['id']}/approve", headers=h, json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_decided"


def test_finance_controller_has_hitl_but_not_kontrakt_red(client, cm, scripted, make_user):
    org, contract, h = cm
    _upload(client, h, contract, _pdf(PAGE1, PAGE2))
    (s,) = client.get(f"/api/contracts/{contract}/suggestions", headers=h).json()
    make_user(org, "fc@test.dk", "finance_controller", password=PW)
    r = client.post(
        f"/api/suggestions/{s['id']}/approve", headers=_login(client, "fc@test.dk"), json={}
    )
    assert r.status_code == 403 and "kontrakt_red" in r.json()["detail"]["error"]


# ---- idempotence, expiry, manual run ---------------------------------------------------------


def test_rerun_updates_instead_of_duplicating_and_new_version_expires(
    client, cm, scripted, Session_
):
    org, contract, h = cm
    doc = _upload(client, h, contract, _pdf(PAGE1, PAGE2)).json()
    r = client.post(f"/api/contracts/{contract}/agents/contract_intake/run", headers=h)
    assert r.status_code == 202
    runs = _runs(client, contract, h)
    assert [r["trigger"] for r in runs] == ["manual", "event"]
    assert runs[0]["suggestions_created"] == 0 and runs[0]["suggestions_updated"] == 1
    assert len(client.get(f"/api/contracts/{contract}/suggestions", headers=h).json()) == 1

    # a new current version of the agreement: old intake suggestion → foraeldet, new one created
    v2 = client.post(
        f"/api/documents/{doc['id']}/versions",
        headers=h,
        files={"file": ("k2.pdf", _pdf(PAGE1 + "\nTillæg", PAGE2), "application/pdf")},
    ).json()
    client.post(f"/api/documents/versions/{v2['id']}/make-current", headers=h)
    sugg = client.get(f"/api/contracts/{contract}/suggestions", headers=h).json()
    assert sorted(s["status"] for s in sugg) == ["foraeldet", "foreslaaet"]


def test_agent_run_without_agreement_documents_is_skipped(client, cm, scripted):
    _, contract, h = cm
    _upload(client, h, contract, _pdf("Driftsrapport Q2"), doc_type="rapport")
    r = client.post(f"/api/contracts/{contract}/agents/contract_intake/run", headers=h)
    assert r.status_code == 202
    (run,) = _runs(client, contract, h)
    assert run["status"] == "sprunget_over" and "aftalegrundlag" in run["error"]
    assert scripted.requests == []


def test_provider_failure_is_a_failed_run_not_a_failed_upload(client, cm):
    _, contract, h = cm
    llm_provider.set_provider(FakeProvider([RuntimeError("boom")]))
    try:
        assert _upload(client, h, contract, _pdf(PAGE1, PAGE2)).status_code == 201
    finally:
        llm_provider.set_provider(None)
    (run,) = _runs(client, contract, h)
    assert run["status"] == "fejlet" and "boom" in run["error"]
    assert client.get(f"/api/contracts/{contract}/suggestions", headers=h).json() == []


def test_refusal_and_budget(client, cm, Session_, monkeypatch):
    from decimal import Decimal

    from app.core.config import settings

    org, contract, h = cm
    llm_provider.set_provider(FakeProvider([FakeResponse("", stop_reason="refusal")]))
    try:
        _upload(client, h, contract, _pdf(PAGE1, PAGE2))
    finally:
        llm_provider.set_provider(None)
    run = _runs(client, contract, h)[0]
    assert run["status"] == "fejlet" and "refusal" in run["error"]

    # budget: a priced model spends; the next call is a hard stop (ADR-0010 §7)
    monkeypatch.setattr(settings, "llm_daily_budget_dkk", Decimal("0.01"))
    with tenant(org, system=True), Session_() as db:
        db.add(
            UsageEvent(
                organization_id=org,
                task="copilot",
                actor_type="human",
                model="claude-opus-5",
                backend="fake",
                cost_dkk=Decimal("1.0"),
            )
        )
        db.commit()
    r = client.post(f"/api/contracts/{contract}/agents/contract_intake/run", headers=h)
    assert r.status_code == 202
    run = _runs(client, contract, h)[0]
    assert run["status"] == "sprunget_over" and run["trigger"] == "manual"


# ---- database-level guarantees ---------------------------------------------------------------


def test_audit_log_is_append_only_for_the_app_role(cm, Session_):
    org, contract, _ = cm
    with tenant(org, system=True), Session_() as db:
        from app.core import audit
        from app.domain.models import AuditAction

        row = audit.record(
            db,
            org_id=org,
            action=AuditAction.contract_created,
            actor=audit.system("System · test"),
            object_kind="contract",
            object_id=contract,
            contract_id=contract,
        )
        db.commit()
        for stmt in (
            "UPDATE audit_log SET object_label = 'x' WHERE id = :i",
            "DELETE FROM audit_log WHERE id = :i",
        ):
            with pytest.raises(Exception, match="permission denied"):
                db.execute(text(stmt), {"i": row.id})
            db.rollback()
        assert row.row_hash and row.prev_hash  # chained onto the fixture's login row


def test_other_tenant_sees_no_suggestions_or_runs(client, cm, scripted, make_org, make_user):
    _, contract, h = cm
    _upload(client, h, contract, _pdf(PAGE1, PAGE2))
    (s,) = client.get(f"/api/contracts/{contract}/suggestions", headers=h).json()
    org_b = make_org("B")
    make_user(org_b, "b@test.dk", "systemadministrator", password=PW)
    h_b = _login(client, "b@test.dk")
    assert client.get(f"/api/contracts/{contract}/suggestions", headers=h_b).status_code == 404
    assert client.get(f"/api/contracts/{contract}/agent-runs", headers=h_b).status_code == 404
    assert (
        client.post(f"/api/suggestions/{s['id']}/approve", headers=h_b, json={}).status_code == 404
    )


def test_llm_run_refuses_without_tenant_context(Session_):
    from pydantic import BaseModel

    from app import llm
    from app.core import audit

    class Out(BaseModel):
        x: int

    with Session_() as db, pytest.raises(llm.LlmContextError):
        llm.run(
            db,
            "contract_intake",
            schema=Out,
            instructions="i",
            material=[],
            question="q",
            org_id=uuid.uuid4(),
            actor=audit.system("t"),
        )
