"""ADR-0010: scheduler, org-level runs, overlap lock, cap/cursor, budget, the
per-organisation switch, alerts and the AI-agenter API."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pymupdf
import pytest
from sqlalchemy import select, text

from app.core.config import settings
from app.core.rls import tenant
from app.domain.models import (
    AgentRun,
    AgentRunStatus,
    AgentSetting,
    AgentTrigger,
    AuditAction,
    AuditLog,
    Contract,
)
from app.jobs import runs, scheduler
from app.llm import provider as llm_provider
from app.llm.provider import FakeProvider, FakeResponse

pytestmark = pytest.mark.integration

PW = "korrekt-adgangskode-123"
CPH = ZoneInfo("Europe/Copenhagen")
ALL_DOC_AGENTS = ("contract_intake", "obligation_extract", "risk_assess", "raci_design")
EMPTY_EXTRACT = json.dumps(
    {"obligations": [], "kpis": [], "price_terms": [], "penalty_terms": [], "rationale": "Intet."}
)


def _login(client, email):
    r = client.post("/api/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _pdf(text_: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text_, fontsize=11)
    data: bytes = doc.tobytes()
    doc.close()
    return data


def _set(Session_, org, key, enabled, override=None):
    with tenant(org, system=True), Session_() as db:
        st = db.get(AgentSetting, (org, key))
        if st is None:
            st = AgentSetting(organization_id=org, agent_key=key)
            db.add(st)
        st.enabled = enabled
        st.schedule_override = override
        db.commit()


def _org_runs(Session_, org, key):
    with tenant(org, system=True), Session_() as db:
        return list(
            db.scalars(
                select(AgentRun)
                .where(AgentRun.agent_key == key, AgentRun.contract_id.is_(None))
                .order_by(AgentRun.started_at)
            ).all()
        )


@pytest.fixture
def two_orgs(client, make_org, make_user, make_contract, Session_):
    """A (two contracts) and B (one) — B must be planned before A (§5)."""
    a = make_org("A")
    b = make_org("B")
    make_user(a, "cm@a.dk", "contract_manager", password=PW)
    make_user(a, "bu@a.dk", "business_user", password=PW)
    make_user(b, "cm@b.dk", "contract_manager", password=PW)
    a1 = make_contract(a, "A-1")
    a2 = make_contract(a, "A-2")
    b1 = make_contract(b, "B-1")
    return a, b, [a1, a2], [b1]


# ---- scheduler ----------------------------------------------------------------------------


def test_plan_orders_smallest_org_first_and_honours_override(two_orgs, Session_):
    a, b, _, _ = two_orgs
    planned = scheduler.plan(datetime(2026, 9, 4, 2, 0, tzinfo=CPH))
    keys = [(p.agent_key, p.org_id) for p in planned]
    assert keys == [("obligation_extract", b), ("obligation_extract", a)]
    # an org moves its run: the override replaces the default cadence for that org only
    _set(Session_, a, "obligation_extract", True, override="30 3 * * *")
    assert [(p.org_id) for p in scheduler.plan(datetime(2026, 9, 4, 2, 0, tzinfo=CPH))] == [b]
    assert [(p.org_id) for p in scheduler.plan(datetime(2026, 9, 4, 3, 30, tzinfo=CPH))] == [a]
    # an invalid override falls back to the default rather than silencing the agent
    _set(Session_, a, "obligation_extract", True, override="not cron")
    assert {p.org_id for p in scheduler.plan(datetime(2026, 9, 4, 2, 0, tzinfo=CPH))} == {a, b}


def test_tick_runs_enabled_agents_and_writes_skip_rows_for_paused_ones(two_orgs, Session_):
    a, b, _, _ = two_orgs
    _set(Session_, a, "obligation_extract", False)
    planned = scheduler.tick(datetime(2026, 9, 4, 2, 0, tzinfo=CPH))
    assert len(planned) == 2
    # B ran: one org-level row, both contracts scanned (no documents → skipped, no model call)
    (rb,) = _org_runs(Session_, b, "obligation_extract")
    assert rb.status == AgentRunStatus.ok
    assert rb.trigger == AgentTrigger.schedule
    assert rb.contracts_scanned == 1
    assert rb.error_context["skipped"] == 1
    assert rb.cost_dkk is None
    # A was paused: a sprunget_over row says so, no agent work happened (ADR-0010 §2/§3)
    (ra,) = _org_runs(Session_, a, "obligation_extract")
    assert ra.status == AgentRunStatus.sprunget_over
    assert ra.error_context == {"reason": "disabled"}
    assert ra.contracts_scanned == 0
    # nothing else fired at 02:00
    assert _org_runs(Session_, a, "risk_assess") == []


def test_cap_and_cursor_continue_where_the_night_stopped(two_orgs, Session_, monkeypatch):
    a, _, (a1, a2), _ = two_orgs
    a3 = None
    with tenant(a, system=True), Session_() as db:
        c = Contract(organization_id=a, reference="A-3", name="Kontrakt A-3")
        db.add(c)
        db.commit()
        a3 = c.id
    monkeypatch.setattr(settings, "agent_contracts_per_run", 2)
    ordered = sorted([a1, a2, a3], key=str)
    runs.run_org(agent_key="risk_assess", org_id=a, trigger=AgentTrigger.schedule)
    runs.run_org(agent_key="risk_assess", org_id=a, trigger=AgentTrigger.schedule)
    runs.run_org(agent_key="risk_assess", org_id=a, trigger=AgentTrigger.schedule)
    r1, r2, r3 = _org_runs(Session_, a, "risk_assess")
    assert (r1.contracts_scanned, r1.error_context.get("reason")) == (2, "cap")
    assert r1.error_context["cursor"] == str(ordered[1])
    assert (r2.contracts_scanned, "cursor" in r2.error_context) == (1, False)
    assert r3.contracts_scanned == 2  # wrapped around: a full pass takes two nights


def test_overlap_is_one_run_and_one_skipped_row(two_orgs, Session_, migrator_engine):
    a, _, _, _ = two_orgs
    key = f"risk_assess:{a}"
    with migrator_engine.connect() as other:
        assert other.execute(text("SELECT pg_try_advisory_lock(hashtext(:k))"), {"k": key}).scalar()
        try:
            rid = runs.run_org(agent_key="risk_assess", org_id=a, trigger=AgentTrigger.manual)
        finally:
            other.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": key})
    with tenant(a, system=True), Session_() as db:
        skipped = db.get(AgentRun, rid)
        assert skipped.status == AgentRunStatus.sprunget_over
        assert skipped.error_context == {"reason": "overlap"}
    # lock released: the next run is real
    rid2 = runs.run_org(agent_key="risk_assess", org_id=a, trigger=AgentTrigger.manual)
    with tenant(a, system=True), Session_() as db:
        assert db.get(AgentRun, rid2).status == AgentRunStatus.ok


def test_one_failed_contract_does_not_stop_the_others(two_orgs, Session_, client):
    a, _, (a1, a2), _ = two_orgs
    h = _login(client, "cm@a.dk")
    for key in ALL_DOC_AGENTS:
        _set(Session_, a, key, False)  # uploads must not consume the script
    for cid in (a1, a2):
        r = client.post(
            f"/api/contracts/{cid}/documents",
            headers=h,
            files={
                "file": ("k.pdf", _pdf("1. Levering\nLeverandøren leverer."), "application/pdf")
            },
            data={"doc_type": "hovedkontrakt", "title": "Hovedkontrakt"},
        )
        assert r.status_code == 201, r.text
    _set(Session_, a, "obligation_extract", True)
    fake = FakeProvider([RuntimeError("provider exploded"), FakeResponse(EMPTY_EXTRACT)])
    llm_provider.set_provider(fake)
    try:
        rid = runs.run_org(agent_key="obligation_extract", org_id=a, trigger=AgentTrigger.schedule)
    finally:
        llm_provider.set_provider(None)
    with tenant(a, system=True), Session_() as db:
        run = db.get(AgentRun, rid)
        assert run.status == AgentRunStatus.ok, run.error_context
        assert run.contracts_scanned == 2
        assert len(run.error_context["failed"]) == 1
        assert "provider exploded" in run.error_context["failed"][0]["error"]
        assert run.input_tokens is not None  # the successful call's usage is on the row
    assert len(fake.requests) == 2


def test_budget_stops_the_night_and_leaves_a_cursor(two_orgs, Session_, client, monkeypatch):
    a, _, (a1, _), _ = two_orgs
    h = _login(client, "cm@a.dk")
    for key in ALL_DOC_AGENTS:
        _set(Session_, a, key, False)
    r = client.post(
        f"/api/contracts/{a1}/documents",
        headers=h,
        files={"file": ("k.pdf", _pdf("1. Levering\nLeverandøren leverer."), "application/pdf")},
        data={"doc_type": "hovedkontrakt", "title": "Hovedkontrakt"},
    )
    assert r.status_code == 201, r.text
    _set(Session_, a, "obligation_extract", True)
    monkeypatch.setattr(settings, "llm_daily_budget_dkk", Decimal("0"))
    llm_provider.set_provider(FakeProvider([FakeResponse(EMPTY_EXTRACT)]))
    try:
        rid = runs.run_org(agent_key="obligation_extract", org_id=a, trigger=AgentTrigger.schedule)
    finally:
        llm_provider.set_provider(None)
    with tenant(a, system=True), Session_() as db:
        run = db.get(AgentRun, rid)
        assert run.status == AgentRunStatus.sprunget_over
        assert run.error_context["reason"] == "budget"
        assert "Døgnbudgettet" in run.error
    # the alert sweep sees it (ADR-0010 §7)
    r = client.get("/api/agents", headers=h)
    by_key = {x["agent_key"]: x for x in r.json()}
    assert "Døgnbudgettet stoppede kørslen" in by_key["obligation_extract"]["alerts"]


# ---- API: AI-agenter ---------------------------------------------------------------------


def test_agents_api_lists_pauses_with_reason_and_runs_now(two_orgs, Session_, client):
    a, _, _, _ = two_orgs
    h = _login(client, "cm@a.dk")
    r = client.get("/api/agents", headers=h)
    assert r.status_code == 200
    rows = {x["agent_key"]: x for x in r.json()}
    assert len(rows) == 7
    assert rows["obligation_extract"]["cadence"] == "0 2 * * *"
    assert rows["contract_intake"]["cadence"] is None
    assert all(x["enabled"] for x in rows.values())

    # pausing needs a reason (afklaring 2: a switch you can flip without a trace is no switch)
    r = client.put("/api/agents/risk_assess/settings", headers=h, json={"enabled": False})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "reason_required"
    r = client.put(
        "/api/agents/risk_assess/settings",
        headers=h,
        json={"enabled": False, "reason": "For mange falske positiver i august"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is False
    assert body["paused_by_name"] == "cm"
    assert body["paused_reason"] == "For mange falske positiver i august"
    assert body["paused_at"] is not None
    with tenant(a, system=True), Session_() as db:
        rows_ = db.scalars(
            select(AuditLog).where(AuditLog.action == AuditAction.agent_settings_changed)
        ).all()
        assert len(rows_) == 1 and rows_[0].details["enabled"] is False

    # a paused agent's "kør nu" writes a skipped row, never runs
    r = client.post("/api/agents/risk_assess/run", headers=h)
    assert r.status_code == 202
    (run,) = _org_runs(Session_, a, "risk_assess")
    assert run.status == AgentRunStatus.sprunget_over and run.trigger == AgentTrigger.manual

    # resume clears who/when/why; an invalid cron override is refused
    r = client.put("/api/agents/risk_assess/settings", headers=h, json={"enabled": True})
    assert r.json()["paused_by_name"] is None and r.json()["paused_reason"] is None
    r = client.put(
        "/api/agents/risk_assess/settings",
        headers=h,
        json={"enabled": True, "schedule_override": "99 * * * *"},
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_cron"
    r = client.put(
        "/api/agents/contract_intake/settings",
        headers=h,
        json={"enabled": True, "schedule_override": "0 3 * * *"},
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "not_scheduled"

    # "kør nu" for a contract agent is one org-wide run
    r = client.post("/api/agents/risk_assess/run", headers=h)
    assert r.status_code == 202
    runs_ = client.get("/api/agents/risk_assess/runs", headers=h).json()
    assert [x["status"] for x in runs_] == ["ok", "sprunget_over"]
    assert runs_[0]["contract_id"] is None

    # unknown agent, and the permission gate
    assert client.post("/api/agents/nope/run", headers=h).status_code == 404
    hb = _login(client, "bu@a.dk")
    assert client.get("/api/agents", headers=hb).status_code == 403
    assert client.post("/api/agents/risk_assess/run", headers=hb).status_code == 403


def test_three_failures_in_a_row_raise_an_alert_on_dashboard_and_agent_page(
    two_orgs, Session_, client
):
    a, _, _, _ = two_orgs
    h = _login(client, "cm@a.dk")
    with tenant(a, system=True), Session_() as db:
        for i in range(3):
            db.add(
                AgentRun(
                    organization_id=a,
                    agent_key="raci_design",
                    trigger=AgentTrigger.schedule,
                    status=AgentRunStatus.fejlet,
                    started_at=datetime.now(UTC) - timedelta(hours=3 - i),
                    finished_at=datetime.now(UTC) - timedelta(hours=3 - i),
                    error="boom",
                )
            )
        db.commit()
    agents = {x["agent_key"]: x for x in client.get("/api/agents", headers=h).json()}
    assert "Tre fejlede kørsler i træk" in agents["raci_design"]["alerts"]
    dash = {x["agent_key"]: x for x in client.get("/api/dashboard", headers=h).json()["agents"]}
    assert dash["raci_design"]["alerts"] == agents["raci_design"]["alerts"]
    assert dash["raci_design"]["runs_failed_7d"] == 3
    # a scheduled agent with runs but no `ok` in 48 h is stale — the other orgs' rows do not count
    assert "Ingen vellykket kørsel i 48 timer" in agents["raci_design"]["alerts"]
    assert agents["obligation_extract"]["alerts"] == []


def test_other_tenant_sees_nothing(two_orgs, client):
    _, _, _, _ = two_orgs
    hb = _login(client, "cm@b.dk")
    runs.run_org(agent_key="risk_assess", org_id=two_orgs[0], trigger=AgentTrigger.manual)
    assert client.get("/api/agents/risk_assess/runs", headers=hb).json() == []
