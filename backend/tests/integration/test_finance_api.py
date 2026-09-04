"""The measurement → breach → claim chain over HTTP (ADR-0019 §5, ADR-0013 §3/§4),
targets and terms as proposals, the report reader, and money masking."""

from __future__ import annotations

import json
import re
from decimal import Decimal

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.rls import tenant
from app.domain.models import AuditLog, Contract
from app.llm import provider as llm_provider
from app.llm.provider import FakeProvider, FakeResponse

pytestmark = pytest.mark.integration

PW = "korrekt-adgangskode-123"

AGREEMENT = (
    "SERVICEAFTALE om IT-drift\n"
    "8. Servicemål\n"
    "8.1 Leverandøren garanterer en oppetid på minimum 99,8 % pr. kalendermåned for kritiske systemer.\n"
    "8.2 Ved oppetid under 99,8 % ifalder leverandøren en service credit på 5 % af det månedlige driftsvederlag."
)
REPORT = "Driftsrapport august 2026\nOppetid, kritiske systemer: 99,62 % i august 2026.\nP1-sager løst inden for 4 timer: 96 %."


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


def _none_intake() -> str:
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


def _extract(doc_id: str) -> str:
    cite = lambda q, p=1: {"document_id": doc_id, "page_pdf": p, "quote": q}  # noqa: E731
    return json.dumps(
        {
            "obligations": [],
            "price_terms": [],
            "kpis": [
                {
                    "name": "Oppetid, kritiske systemer",
                    "unit": "pct",
                    "target_operator": "gte",
                    "target_value": "99.8",
                    "target_value_high": None,
                    "period": "maaned",
                    "confidence": "hoej",
                    "citation": cite("garanterer en oppetid på minimum 99,8 % pr. kalendermåned"),
                }
            ],
            "penalty_terms": [
                {
                    "name": "Service credit ved manglende oppetid",
                    "term_type": "service_credit_pct_of_fee",
                    "trigger_description": "oppetid under 99,8 %",
                    "applies_to_kpi": "Oppetid, kritiske systemer",
                    "rate": "0.05",
                    "tiers": None,
                    "basis": "maanedligt_driftsvederlag",
                    "basis_amount": None,
                    "time_unit": "maaned",
                    "cap_rate": None,
                    "cap_amount": None,
                    "confidence": "hoej",
                    "citation": cite("service credit på 5 % af det månedlige driftsvederlag"),
                }
            ],
            "rationale": "Oppetidsmål og service credit står ordret i pkt. 8.",
        }
    )


class _Router(FakeProvider):
    def complete(self, req):
        m = re.search(r'<dokument id="([^"]+)"', req.material)
        doc_id = m.group(1) if m else "?"
        if "KPI/SLA Agent" in req.system:
            k = re.search(r"^([0-9a-f-]{36}) \| Oppetid", req.material, re.M)
            text = json.dumps(
                {
                    "measurements": [
                        {
                            "kpi_id": k.group(1) if k else "?",
                            "period_start": "2026-08-01",
                            "value": "99.62",
                            "confidence": "hoej",
                            "citation": {
                                "document_id": doc_id,
                                "page_pdf": 1,
                                "quote": "Oppetid, kritiske systemer: 99,62 % i august 2026",
                            },
                        }
                    ],
                    "rationale": "Rapporten dækker august.",
                }
            )
        elif "Obligation Extraction Agent" in req.system:
            text = _extract(doc_id)
        elif "Risk Agent" in req.system:
            text = json.dumps({"risks": [], "rationale": "-"})
        else:
            text = _none_intake()
        self.responses.append(FakeResponse(text))
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
    make_user(org, "cm@test.dk", "contract_manager", password=PW)  # kontrakt_red + hitl
    make_user(org, "fc@test.dk", "finance_controller", password=PW)  # okonomi + hitl
    make_user(org, "co@test.dk", "contract_owner", password=PW)  # okonomi + hitl, the 2nd signature
    contract = make_contract(org, "K-2026-001")
    with tenant(org, system=True), Session_() as db:
        db.get(Contract, contract).annual_value = Decimal("7350000.00")  # 612.500 kr./month
        db.commit()
    return (
        org,
        contract,
        _login(client, "cm@test.dk"),
        _login(client, "fc@test.dk"),
        _login(client, "co@test.dk"),
    )


def _upload(client, h, contract, data, doc_type="hovedkontrakt", title="Hovedkontrakt"):
    return client.post(
        f"/api/contracts/{contract}/documents",
        headers=h,
        files={"file": ("k.pdf", data, "application/pdf")},
        data={"doc_type": doc_type, "title": title},
    )


def _suggestions(client, contract, h, kind):
    return [
        s
        for s in client.get(f"/api/contracts/{contract}/suggestions", headers=h).json()
        if s["subject_kind"] == kind and s["status"] == "foreslaaet"
    ]


def _setup_kpi_and_term(client, contract, h_cm, h_fc):
    assert _upload(client, h_cm, contract, _pdf(AGREEMENT)).status_code == 201
    (k,) = _suggestions(client, contract, h_cm, "kpi")
    (t,) = _suggestions(client, contract, h_cm, "penalty_term")
    assert k["payload"]["target_text"] == "≥ 99,8 %" and k["citations"][0]["clause_ref"] == "8.1"
    # money parameters need okonomi (ADR-0013 §1): the manager cannot approve the term
    assert (
        client.post(f"/api/suggestions/{t['id']}/approve", headers=h_cm, json={}).status_code == 403
    )
    assert (
        client.post(f"/api/suggestions/{k['id']}/approve", headers=h_cm, json={}).status_code == 200
    )
    assert (
        client.post(f"/api/suggestions/{t['id']}/approve", headers=h_fc, json={}).status_code == 200
    )
    (kpi,) = client.get(f"/api/contracts/{contract}/kpis", headers=h_cm).json()
    (term,) = client.get(f"/api/contracts/{contract}/penalty-terms", headers=h_cm).json()
    return kpi, term


# ---- targets and terms from the agreement ---------------------------------------------------


def test_targets_and_terms_are_proposed_approved_and_linked(client, world, router):
    _, contract, h_cm, h_fc, _ = world
    kpi, term = _setup_kpi_and_term(client, contract, h_cm, h_fc)
    assert (
        kpi["ref"] == "K-1" and kpi["target_text"] == "≥ 99,8 %" and Decimal(kpi["warn_band"]) == 1
    )
    assert kpi["status"]["color"] == "graa" and kpi["status"]["reason"] == "data mangler"
    assert kpi["citations"][0]["clause_ref"] == "8.1"
    assert (
        term["ref"] == "B-1"
        and Decimal(term["rate"]) == Decimal("0.05")
        and term["status"] == "aktiv"
    )
    assert kpi["penalty_term_id"] == term["id"]  # linked by name (either approval order)


# ---- the chain: measurement → breach → claim -------------------------------------------------


def test_measurement_below_target_creates_breach_and_computed_claim(
    client, world, router, Session_
):
    org, contract, h_cm, h_fc, _ = world
    kpi, term = _setup_kpi_and_term(client, contract, h_cm, h_fc)
    r = client.post(
        f"/api/kpis/{kpi['id']}/measurements",
        headers=h_cm,
        json={"period_start": "2026-08-01", "value": "99.62"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert (
        body["measurement"]["period_end"] == "2026-08-31"
        and body["measurement"]["source_kind"] == "manual"
    )
    assert (
        Decimal(body["breach"]["actual_value"]) == Decimal("99.62") and body["breach"]["claim_id"]
    )
    assert body["claim"]["amount"] is None  # the manager lacks okonomi → masked, not 0
    assert (
        body["claim"]["status"] == "beregnet"
        and body["claim"]["requires_second_signature"] is False
    )

    (claim,) = client.get(f"/api/contracts/{contract}/claims", headers=h_fc).json()
    assert claim["ref"] == "KR-1" and claim["claim_type"] == "service_credit"
    assert claim["amount"] == "30625.00" and claim["amount_uncapped"] == "30625.00"  # mockup SLA-B1
    assert claim["basis_text"].startswith(
        "5 % af månedligt driftsvederlag (612.500,00 kr.) = 30.625,00 kr. — jf. Hovedkontrakt · s. 1 · pkt. 8.2"
    )
    assert claim["citations"][0]["clause_ref"] == "8.2" and claim["created_by"] is None  # system
    (k,) = client.get(f"/api/contracts/{contract}/kpis", headers=h_cm).json()
    assert k["status"]["color"] == "roed" and Decimal(k["status"]["value"]) == Decimal("99.62")
    (b,) = client.get(f"/api/contracts/{contract}/sla-breaches", headers=h_cm).json()
    assert b["period_start"] == "2026-08-01"

    with tenant(org, system=True), Session_() as db:
        actions = [a.action.value for a in db.scalars(select(AuditLog))]
        for a in ("measurement_recorded", "sla_breach_recorded", "claim_calculated"):
            assert a in actions

    # recompute gives the same number (ADR-0013 §3)
    rc = client.post(f"/api/claims/{claim['id']}/recompute", headers=h_fc).json()
    assert rc["matches_stored"] is True and rc["amount"] == "30625.00"


def test_claim_lifecycle_and_permissions(client, world, router):
    _, contract, h_cm, h_fc, _ = world
    kpi, _ = _setup_kpi_and_term(client, contract, h_cm, h_fc)
    client.post(
        f"/api/kpis/{kpi['id']}/measurements",
        headers=h_cm,
        json={"period_start": "2026-08-01", "value": "99.0"},
    )
    (claim,) = client.get(f"/api/contracts/{contract}/claims", headers=h_fc).json()
    assert (
        client.post(f"/api/claims/{claim['id']}/approve", headers=h_cm, json={}).status_code == 403
    )
    assert (
        client.post(f"/api/claims/{claim['id']}/submit", headers=h_fc, json={}).status_code == 409
    )  # not approved yet
    r = client.post(f"/api/claims/{claim['id']}/approve", headers=h_fc, json={"comment": "ok"})
    assert r.status_code == 200 and r.json()["status"] == "godkendt"
    r = client.post(f"/api/claims/{claim['id']}/submit", headers=h_fc, json={})
    assert r.json()["status"] == "fremsat" and r.json()["submitted_at"]
    r = client.post(
        f"/api/claims/{claim['id']}/settle",
        headers=h_fc,
        json={"status": "modregnet", "comment": "faktura 10493"},
    )
    assert r.json()["status"] == "modregnet"
    r = client.post(f"/api/claims/{claim['id']}/settle", headers=h_fc, json={"status": "betalt"})
    assert r.status_code == 409  # modregnet is final


def test_superseding_a_measurement_needs_a_reason_and_recomputes(client, world, router):
    _, contract, h_cm, h_fc, _ = world
    kpi, _ = _setup_kpi_and_term(client, contract, h_cm, h_fc)
    m = f"/api/kpis/{kpi['id']}/measurements"
    client.post(
        m, headers=h_cm, json={"period_start": "2026-07-01", "value": "99.0"}
    )  # → KR-1 beregnet
    r = client.post(m, headers=h_cm, json={"period_start": "2026-07-01", "value": "99.95"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "reason_required"
    r = client.post(
        m,
        headers=h_cm,
        json={
            "period_start": "2026-07-01",
            "value": "99.95",
            "note": "korrigeret rapport fra leverandøren",
        },
    )
    assert r.status_code == 201 and r.json()["breach"] is None
    claims = client.get(f"/api/contracts/{contract}/claims", headers=h_fc).json()
    assert [c["status"] for c in claims] == ["frafaldet"]  # the open claim lost its basis
    assert "måling erstattet" in claims[0]["decision_comment"]
    (k,) = client.get(f"/api/contracts/{contract}/kpis", headers=h_cm).json()
    live = [x for x in k["measurements"] if x["superseded_by_id"] is None]
    old = [x for x in k["measurements"] if x["superseded_by_id"] is not None]
    assert len(live) == 1 and len(old) == 1 and live[0]["supersedes_measurement_id"] == old[0]["id"]


def test_large_claim_needs_two_signatures_the_second_a_contract_owner(client, world, router):
    _, contract, h_cm, h_fc, h_co = world
    term = client.post(
        f"/api/contracts/{contract}/penalty-terms",
        headers=h_fc,
        json={
            "name": "Fast bod ved P1-brud",
            "term_type": "fixed_penalty_per_breach",
            "basis": "fast_beloeb",
            "basis_amount": "300000",
            "time_unit": "haendelse",
        },
    ).json()
    assert term["ref"] == "B-1" and term["origin"] == "human"
    kpi = client.post(
        f"/api/contracts/{contract}/kpis",
        headers=h_cm,
        json={
            "name": "P1-løsningstid inden for 4 timer",
            "unit": "pct",
            "target_operator": "gte",
            "target_value": "95",
            "period": "maaned",
            "penalty_term_id": term["id"],
        },
    ).json()
    r = client.post(
        f"/api/kpis/{kpi['id']}/measurements",
        headers=h_cm,
        json={"period_start": "2026-08-01", "value": "90"},
    )
    assert r.status_code == 201 and r.json()["claim"]["requires_second_signature"] is True
    (claim,) = client.get(f"/api/contracts/{contract}/claims", headers=h_fc).json()
    assert claim["amount"] == "300000.00"
    r = client.post(f"/api/claims/{claim['id']}/approve", headers=h_fc, json={})
    assert r.json()["status"] == "afventer_2_signatur"
    r = client.post(f"/api/claims/{claim['id']}/approve", headers=h_fc, json={})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "second_signature_same_user"
    assert (
        client.post(f"/api/claims/{claim['id']}/approve", headers=h_cm, json={}).status_code == 403
    )
    r = client.post(f"/api/claims/{claim['id']}/approve", headers=h_co, json={})
    assert (
        r.status_code == 200 and r.json()["status"] == "godkendt" and r.json()["second_approved_by"]
    )


def test_breach_without_term_parameters_is_recorded_without_a_claim(client, world, router):
    _, contract, h_cm, _, _ = world
    kpi = client.post(
        f"/api/contracts/{contract}/kpis",
        headers=h_cm,
        json={
            "name": "Oppetid, WAN",
            "unit": "pct",
            "target_operator": "gte",
            "target_value": "99.9",
            "period": "maaned",
        },
    ).json()
    r = client.post(
        f"/api/kpis/{kpi['id']}/measurements",
        headers=h_cm,
        json={"period_start": "2026-08-01", "value": "99.5"},
    )
    assert r.status_code == 201 and r.json()["claim"] is None
    assert "ingen bodsklausul" in r.json()["breach"]["note"]
    # a measurement on a non-period boundary is refused
    r = client.post(
        f"/api/kpis/{kpi['id']}/measurements",
        headers=h_cm,
        json={"period_start": "2026-08-15", "value": "99.5"},
    )
    assert r.status_code == 400 and r.json()["detail"]["code"] == "bad_period"


# ---- the report reader (ADR-0019 §2 `document`) -----------------------------------------------


def test_report_upload_proposes_measurement_and_approval_runs_the_chain(client, world, router):
    _, contract, h_cm, h_fc, _ = world
    _setup_kpi_and_term(client, contract, h_cm, h_fc)
    assert (
        _upload(
            client, h_cm, contract, _pdf(REPORT), doc_type="rapport", title="Driftsrapport august"
        ).status_code
        == 201
    )
    runs = [
        r
        for r in client.get(f"/api/contracts/{contract}/agent-runs", headers=h_cm).json()
        if r["agent_key"] == "kpi_parse"
    ]
    assert runs and runs[0]["status"] == "ok" and runs[0]["suggestions_created"] == 1, runs
    (s,) = _suggestions(client, contract, h_cm, "kpi_measurement")
    assert (
        s["payload"]["value"] == "99.62"
        and s["citations"][0]["verified"]
        and s["confidence"] == "hoej"
    )
    r = client.post(f"/api/suggestions/{s['id']}/approve", headers=h_cm, json={})
    assert r.status_code == 200, r.text
    assert "SLA-brud registreret, krav KR-1 beregnet (30625.00 kr.)" in r.json()["decision_comment"]
    (k,) = client.get(f"/api/contracts/{contract}/kpis", headers=h_cm).json()
    assert k["measurements"][0]["source_kind"] == "document" and k["status"]["color"] == "roed"
    (claim,) = client.get(f"/api/contracts/{contract}/claims", headers=h_fc).json()
    assert claim["amount"] == "30625.00"
