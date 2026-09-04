"""GET /api/dashboard — Overblik as a pure roll-up (ADR-0001 §Overblik, bidflow
ADR-0040): no dashboard tables, every number is a query over the register the
caller may see (RLS), computed at read time.

  counts      contracts, obligations (overdue derived), risks (level derived), queue
  actions     "Kræver handling" = open ai_suggestions (ADR-0004 §Konsekvenser: one
              query, one queue) with `can_decide` per the caller's permissions
  deadlines   ADR-0017 §1's derived queue, first cut: opsigelse · udloeb ·
              forpligtelse · risiko, ±window, severity by kind and criticality
  agents      ADR-0010: last run per agent, on/off, failures in 7 days
  ai_spend    ADR-0014 §4, only with `okonomi`
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents import AGENTS
from app.ai.suggestions import SUBJECT_PERMISSION
from app.api.schemas import (
    ActionItem,
    AgentStatus,
    AiSpend,
    DashboardCounts,
    DashboardOut,
    DeadlineItem,
)
from app.core import access
from app.core.auth import Principal, current_principal, tenant_session
from app.domain.models import (
    OPEN_SUGGESTION_STATUSES,
    AgentRun,
    AgentRunStatus,
    AgentSetting,
    AiSuggestion,
    Confidentiality,
    Contract,
    ContractStatus,
    ContractTier,
    Criticality,
    Obligation,
    ObligationStatus,
    Risk,
    RiskStatus,
    SuggestionSubject,
    UsageEvent,
    risk_level_for,
)

router = APIRouter(prefix="/api", tags=["dashboard"])

_SEV = {"lav": 0, "mellem": 1, "hoej": 2}


def _contract_severity(c: Contract) -> str:
    return "hoej" if c.tier in (ContractTier.N1, ContractTier.N2) else "mellem"


def _title_for(s: AiSuggestion) -> str:
    if s.subject_kind == SuggestionSubject.contract_intake:
        return "Stamdata fra aftalegrundlaget"
    return str(s.payload.get("title") or s.subject_kind.value)


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(
    window_days: int = Query(default=180, ge=1, le=730),
    principal: Principal = Depends(current_principal),
    session: Session = Depends(tenant_session),
) -> DashboardOut:
    today = date.today()
    horizon = today + timedelta(days=window_days)
    contracts = {
        c.id: c
        for c in session.scalars(
            select(Contract).where(Contract.status != ContractStatus.arkiveret)
        ).all()
    }

    # ---- counts -------------------------------------------------------------------------
    obligations = session.scalars(
        select(Obligation).where(Obligation.status == ObligationStatus.aaben)
    ).all()
    risks = session.scalars(select(Risk).where(Risk.status != RiskStatus.lukket)).all()
    open_suggestions = session.scalars(
        select(AiSuggestion)
        .where(AiSuggestion.status.in_(OPEN_SUGGESTION_STATUSES))
        .order_by(AiSuggestion.created_at.desc())
    ).all()
    week_ago = datetime.now(UTC) - timedelta(days=7)
    failed_runs = session.scalars(
        select(AgentRun).where(
            AgentRun.status == AgentRunStatus.fejlet, AgentRun.started_at >= week_ago
        )
    ).all()
    counts = DashboardCounts(
        contracts_total=len(contracts),
        contracts_active=sum(1 for c in contracts.values() if c.status == ContractStatus.aktiv),
        contracts_draft=sum(1 for c in contracts.values() if c.status == ContractStatus.kladde),
        contracts_fortrolig=sum(
            1 for c in contracts.values() if c.confidentiality == Confidentiality.fortrolig
        ),
        obligations_open=len(obligations),
        obligations_overdue=sum(
            1 for o in obligations if o.deadline is not None and o.deadline < today
        ),
        risks_open=len(risks),
        risks_high=sum(
            1 for r in risks if risk_level_for(r.probability * r.consequence).value == "hoej"
        ),
        suggestions_open=len(open_suggestions),
        agent_runs_failed_7d=len(failed_runs),
    )

    # ---- kræver handling (ADR-0004: one query) ---------------------------------------------
    can_hitl = principal.can(access.HITL)
    actions = [
        ActionItem(
            suggestion_id=s.id,
            contract_id=s.contract_id,
            contract_ref=contracts[s.contract_id].reference if s.contract_id in contracts else "",
            contract_name=contracts[s.contract_id].name if s.contract_id in contracts else "",
            subject_kind=s.subject_kind,
            title=_title_for(s),
            confidence=s.confidence,
            agent_key=s.agent_key,
            created_at=s.created_at,
            can_decide=can_hitl
            and (
                SUBJECT_PERMISSION.get(s.subject_kind) is None
                or principal.can(SUBJECT_PERMISSION[s.subject_kind])
            ),
        )
        for s in open_suggestions[:100]
    ]

    # ---- deadlines (ADR-0017 §1, derived) ---------------------------------------------------
    deadlines: list[DeadlineItem] = []

    def add(
        kind: str,
        c: Contract,
        due: date | None,
        label: str,
        severity: str,
        subject_id: uuid.UUID | None = None,
    ) -> None:
        if due is None or due > horizon:
            return
        deadlines.append(
            DeadlineItem(
                kind=kind,
                contract_id=c.id,
                contract_ref=c.reference,
                contract_name=c.name,
                label=label,
                due_date=due,
                days_left=(due - today).days,
                severity=severity,
                subject_id=subject_id,
            )
        )

    for c in contracts.values():
        if c.status in (ContractStatus.udloebet, ContractStatus.opsagt):
            continue
        add("opsigelse", c, c.last_termination_date, "Sidste opsigelsesdato", _contract_severity(c))
        add("udloeb", c, c.end_date, "Kontrakten udløber", _contract_severity(c))
    for o in obligations:
        oc = contracts.get(o.contract_id)
        if oc is None:
            continue
        sev = "hoej" if o.criticality in (Criticality.hoej, Criticality.kritisk) else "mellem"
        add("forpligtelse", oc, o.deadline, f"F-{o.seq} {o.title}", sev, o.id)
    for r in risks:
        rc = contracts.get(r.contract_id)
        if rc is None:
            continue
        add(
            "risiko",
            rc,
            r.deadline,
            f"R-{r.seq} {r.title}",
            risk_level_for(r.probability * r.consequence).value,
            r.id,
        )
    deadlines.sort(key=lambda d: (d.due_date, -_SEV[d.severity]))

    # ---- agents (ADR-0010 §2/§3) -----------------------------------------------------------
    settings_by_key = {s.agent_key: s for s in session.scalars(select(AgentSetting)).all()}
    agents: list[AgentStatus] = []
    for key, mod in AGENTS.items():
        last = session.scalars(
            select(AgentRun)
            .where(AgentRun.agent_key == key, AgentRun.status != AgentRunStatus.koerer)
            .order_by(AgentRun.started_at.desc())
            .limit(1)
        ).first()
        setting = settings_by_key.get(key)
        agents.append(
            AgentStatus(
                agent_key=key,
                label=str(getattr(mod, "LABEL", key)).removeprefix("AI · "),
                enabled=setting.enabled if setting is not None else True,
                last_run_at=last.started_at if last else None,
                last_status=last.status if last else None,
                last_findings=(last.suggestions_created + last.suggestions_updated)
                if last
                else None,
                runs_failed_7d=sum(1 for r in failed_runs if r.agent_key == key),
            )
        )

    # ---- money: only with okonomi (ADR-0003 §2, ADR-0014 §4) --------------------------------
    portfolio_annual_value: Decimal | None = None
    ai_spend: AiSpend | None = None
    if principal.can(access.OKONOMI):
        portfolio_annual_value = sum(
            (c.annual_value for c in contracts.values() if c.annual_value is not None),
            Decimal("0"),
        )
        month_start = datetime(today.year, today.month, 1, tzinfo=UTC)
        rows = session.execute(
            select(
                UsageEvent.task,
                func.coalesce(func.sum(UsageEvent.cost_dkk), 0),
                func.coalesce(func.sum(UsageEvent.cost_usd), 0),
            )
            .where(UsageEvent.occurred_at >= month_start)
            .group_by(UsageEvent.task)
        ).all()
        by_task = {str(t): Decimal(dkk) for t, dkk, _ in rows}
        ai_spend = AiSpend(
            month_dkk=sum(by_task.values(), Decimal("0")),
            month_usd=sum((Decimal(usd) for _, _, usd in rows), Decimal("0")),
            by_task=by_task,
        )

    return DashboardOut(
        counts=counts,
        actions=actions,
        deadlines=deadlines[:50],
        agents=agents,
        portfolio_annual_value=portfolio_annual_value,
        ai_spend=ai_spend,
        window_days=window_days,
    )
