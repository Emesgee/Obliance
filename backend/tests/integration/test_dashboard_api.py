"""GET /api/dashboard — a roll-up over what the caller may see: counts, the one
HITL queue, derived deadlines, agent status, money only with okonomi."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.core.rls import tenant
from app.domain.models import (
    AgentRun,
    AgentRunStatus,
    AgentSetting,
    AgentTrigger,
    AiSuggestion,
    Confidence,
    Contract,
    ContractStatus,
    ContractTier,
    Criticality,
    Obligation,
    ObligationFrequency,
    ObligationParty,
    Origin,
    Risk,
    RiskCategory,
    SuggestionKind,
    SuggestionSubject,
    UsageEvent,
)

pytestmark = pytest.mark.integration

PW = "korrekt-adgangskode-123"


def _login(client: TestClient, email: str) -> dict[str, str]:
    r = client.post("/api/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def world(client, make_org, make_user, make_contract, Session_):
    """Two contracts (one N1 expiring soon, one fortrolig), an overdue and an
    upcoming obligation, a high risk, two open suggestions, runs and usage."""
    org = make_org("A")
    co = make_user(
        org, "co@test.dk", "contract_owner", password=PW
    )  # hitl + okonomi, no kontrakt_red
    make_user(org, "cm@test.dk", "contract_manager", password=PW)  # kontrakt_red + hitl, no okonomi
    make_user(org, "bu@test.dk", "business_user", password=PW)
    today = date.today()
    c1 = make_contract(org, "K-2026-001")
    # fortrolig: visible to its manager (the owner user) only — RLS level 2 (ADR-0002)
    c2 = make_contract(org, "R-2026-001", confidentiality="fortrolig", manager_id=co)
    with tenant(org, system=True), Session_() as db:
        a = db.get(Contract, c1)
        a.status = ContractStatus.aktiv
        a.tier = ContractTier.N1
        a.end_date = today + timedelta(days=40)
        a.last_termination_date = today + timedelta(days=10)
        a.annual_value = Decimal("6125000.00")
        b = db.get(Contract, c2)
        b.annual_value = Decimal("1000000.00")
        b.end_date = today + timedelta(days=400)  # outside the default window
        db.add_all(
            [
                Obligation(
                    organization_id=org,
                    contract_id=c1,
                    seq=1,
                    title="Driftsrapport Q2",
                    party=ObligationParty.leverandoer,
                    frequency=ObligationFrequency.kvartalsvis,
                    deadline=today - timedelta(days=3),
                    criticality=Criticality.hoej,
                    origin=Origin.human,
                ),
                Obligation(
                    organization_id=org,
                    contract_id=c1,
                    seq=2,
                    title="ISAE 3402-erklæring",
                    party=ObligationParty.leverandoer,
                    frequency=ObligationFrequency.aarlig,
                    deadline=today + timedelta(days=25),
                    criticality=Criticality.mellem,
                    origin=Origin.human,
                ),
                Risk(
                    organization_id=org,
                    contract_id=c1,
                    seq=1,
                    title="Prisregulering uden loft",
                    category=RiskCategory.kommerciel,
                    probability=4,
                    consequence=4,
                    origin=Origin.human,
                ),
                AiSuggestion(
                    organization_id=org,
                    contract_id=c1,
                    agent_key="obligation_extract",
                    kind=SuggestionKind.create,
                    subject_kind=SuggestionSubject.obligation,
                    payload={"title": "Levere kvartalsvis rapport"},
                    confidence=Confidence.hoej,
                    citations=[{"kind": "document"}],
                    fingerprint="fp-1",
                ),
                AiSuggestion(
                    organization_id=org,
                    contract_id=c2,
                    agent_key="contract_intake",
                    kind=SuggestionKind.update,
                    subject_kind=SuggestionSubject.contract_intake,
                    subject_id=c2,
                    payload={"fields": {}},
                    confidence=Confidence.mellem,
                    fingerprint="fp-2",
                ),
                AgentRun(
                    organization_id=org,
                    agent_key="risk_assess",
                    contract_id=c1,
                    trigger=AgentTrigger.event,
                    status=AgentRunStatus.fejlet,
                    error="boom",
                    started_at=datetime.now(UTC),
                    finished_at=datetime.now(UTC),
                ),
                AgentRun(
                    organization_id=org,
                    agent_key="obligation_extract",
                    contract_id=c1,
                    trigger=AgentTrigger.manual,
                    status=AgentRunStatus.ok,
                    suggestions_created=3,
                    started_at=datetime.now(UTC) - timedelta(hours=1),
                    finished_at=datetime.now(UTC),
                ),
                AgentSetting(organization_id=org, agent_key="contract_intake", enabled=False),
                UsageEvent(
                    organization_id=org,
                    task="obligation_extract",
                    actor_type="agent",
                    model="m",
                    backend="fake",
                    cost_dkk=Decimal("0.4300"),
                    cost_usd=Decimal("0.062"),
                ),
                UsageEvent(
                    organization_id=org,
                    task="risk_assess",
                    actor_type="agent",
                    model="m",
                    backend="fake",
                    cost_dkk=Decimal("0.6000"),
                    cost_usd=Decimal("0.087"),
                ),
            ]
        )
        db.commit()
    return org, c1, c2


def test_owner_sees_full_rollup_with_money(client, world):
    _, c1, c2 = world
    d = client.get("/api/dashboard", headers=_login(client, "co@test.dk")).json()
    c = d["counts"]
    assert c["contracts_total"] == 2 and c["contracts_active"] == 1 and c["contracts_draft"] == 1
    assert c["contracts_fortrolig"] == 1
    assert c["obligations_open"] == 2 and c["obligations_overdue"] == 1
    assert c["risks_open"] == 1 and c["risks_high"] == 1  # 4 × 4 = 16 → hoej, derived
    assert c["suggestions_open"] == 2 and c["agent_runs_failed_7d"] == 1

    # the queue: owner has hitl but not kontrakt_red → cannot decide either subject
    assert {a["subject_kind"] for a in d["actions"]} == {"obligation", "contract_intake"}
    assert all(a["can_decide"] is False for a in d["actions"])

    # deadlines: sorted by date; overdue first; severity by kind/criticality/tier
    kinds = [(x["kind"], x["days_left"], x["severity"]) for x in d["deadlines"]]
    assert kinds == [
        ("forpligtelse", -3, "hoej"),
        ("opsigelse", 10, "hoej"),  # N1 contract
        ("forpligtelse", 25, "mellem"),
        ("udloeb", 40, "hoej"),
    ]
    assert d["deadlines"][0]["label"] == "F-1 Driftsrapport Q2"
    assert all(x["contract_id"] == str(c1) for x in d["deadlines"])  # c2's udloeb is outside 180 d

    # agents: paused, failed, ok
    agents = {a["agent_key"]: a for a in d["agents"]}
    assert agents["contract_intake"]["enabled"] is False
    assert (
        agents["risk_assess"]["last_status"] == "fejlet"
        and agents["risk_assess"]["runs_failed_7d"] == 1
    )
    assert (
        agents["obligation_extract"]["last_findings"] == 3
        and agents["obligation_extract"]["enabled"]
    )

    # money with okonomi
    assert d["portfolio_annual_value"] == "7125000.00"
    assert (
        d["ai_spend"]["month_dkk"] == "1.0300"
        and d["ai_spend"]["by_task"]["risk_assess"] == "0.6000"
    )


def test_manager_can_decide_but_sees_no_money(client, world):
    _, c1, _ = world
    d = client.get("/api/dashboard", headers=_login(client, "cm@test.dk")).json()
    assert d["portfolio_annual_value"] is None and d["ai_spend"] is None
    assert [a["contract_id"] for a in d["actions"]] == [str(c1)]  # not the fortrolig one
    assert all(a["can_decide"] is True for a in d["actions"])


def test_business_user_rollup_is_rls_scoped(client, world):
    _, c1, c2 = world
    d = client.get("/api/dashboard", headers=_login(client, "bu@test.dk")).json()
    assert d["counts"]["contracts_total"] == 1 and d["counts"]["contracts_fortrolig"] == 0
    assert d["counts"]["suggestions_open"] == 1  # the fortrolig contract's proposal is invisible
    assert [a["contract_id"] for a in d["actions"]] == [str(c1)]
    assert d["portfolio_annual_value"] is None


def test_window_parameter(client, world):
    d = client.get("/api/dashboard?window_days=730", headers=_login(client, "co@test.dk")).json()
    assert any(x["kind"] == "udloeb" and x["days_left"] == 400 for x in d["deadlines"])
    assert (
        client.get("/api/dashboard?window_days=0", headers=_login(client, "co@test.dk")).status_code
        == 422
    )
