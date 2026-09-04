"""The job functions the queue runs — one per (agent, org), or one per contract.

    run_org(agent_key=, org_id=, trigger=)          nightly / manual, ADR-0010 §1 §4 §5
    run_contract(agent_key=, org_id=, contract_id=, trigger=)   event / manual

Module-level functions, so RQ can address them by import path. Both are
self-contained: own session, system context (ADR-0002), the `agent_runs` row is
the report (ADR-0010 §3) and nothing is raised to the caller.

`run_org` takes an advisory lock per (agent, org) — a second job for the same
pair while one runs writes a `sprunget_over · overlap` row instead of running
twice (§4). It iterates the organisation's active contracts in id order, at most
`agent_contracts_per_run` per run (§5); a hit cap or a hit budget leaves a
cursor in `error_context` and the next run continues from there. One contract's
failure is logged into the run and never stops the others (§6 step 4).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import llm
from app.agents import AGENTS, runtime
from app.agents.definitions import BY_KEY, AgentDefinition
from app.core.config import settings
from app.core.db import SessionLocal, engine
from app.core.rls import tenant
from app.domain.models import AgentRun, AgentRunStatus, AgentTrigger, Contract, ContractStatus

log = logging.getLogger(__name__)

# "Kontrakter under overvågning": drafts and active contracts; expired, terminated and
# archived ones are left alone by the night.
MONITORED = (ContractStatus.kladde, ContractStatus.aktiv)


def run_contract(
    *,
    agent_key: str,
    org_id: uuid.UUID,
    contract_id: uuid.UUID,
    trigger: AgentTrigger,
    trigger_ref: str | None = None,
    triggered_by: uuid.UUID | None = None,
) -> uuid.UUID:
    agent = AGENTS[agent_key]
    rid: uuid.UUID = agent.run_for_contract(
        org_id=org_id,
        contract_id=contract_id,
        trigger=trigger,
        trigger_ref=trigger_ref,
        triggered_by=triggered_by,
    )
    return rid


@contextmanager
def org_lock(agent_key: str, org_id: uuid.UUID) -> Generator[bool, None, None]:
    """Session-level advisory lock on hash(agent:org). Held on its own connection
    for the duration; released explicitly — a session lock survives ROLLBACK and
    would otherwise stay with the pooled connection."""
    key = f"{agent_key}:{org_id}"
    with engine.connect() as conn:
        got = bool(
            conn.execute(text("SELECT pg_try_advisory_lock(hashtext(:k))"), {"k": key}).scalar()
        )
        try:
            yield got
        finally:
            if got:
                conn.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": key})
            conn.rollback()


def skip_row(
    *,
    agent_key: str,
    org_id: uuid.UUID,
    trigger: AgentTrigger,
    reason: str,
    error: str | None = None,
    triggered_by: uuid.UUID | None = None,
) -> uuid.UUID:
    """A `sprunget_over` row without a run — a paused agent, an overlap. Written so
    an agent that has not run for three weeks is visible (ADR-0010 §3)."""
    d = BY_KEY.get(agent_key)
    now = datetime.now(UTC)
    with tenant(org_id, system=True), SessionLocal() as s:
        run = AgentRun(
            organization_id=org_id,
            agent_key=agent_key,
            contract_id=None,
            trigger=trigger,
            triggered_by=triggered_by,
            task=d.task if d else None,
            status=AgentRunStatus.sprunget_over,
            started_at=now,
            finished_at=now,
            duration_ms=0,
            error=error,
            error_context={"reason": reason},
        )
        s.add(run)
        s.commit()
        return run.id


def run_org(
    *,
    agent_key: str,
    org_id: uuid.UUID,
    trigger: AgentTrigger,
    triggered_by: uuid.UUID | None = None,
) -> uuid.UUID:
    d = BY_KEY.get(agent_key)
    agent = AGENTS.get(agent_key)
    if d is None or agent is None:
        raise KeyError(f"Ukendt agent: {agent_key}")
    with org_lock(agent_key, org_id) as acquired:
        if not acquired:
            log.warning("agent=%s org=%s overlap: a run is already in progress", agent_key, org_id)
            return skip_row(
                agent_key=agent_key,
                org_id=org_id,
                trigger=trigger,
                reason="overlap",
                error="En kørsel for denne agent er allerede i gang",
                triggered_by=triggered_by,
            )
        if d.scope == "org":
            rid: uuid.UUID = agent.run_for_org(
                org_id=org_id, trigger=trigger, triggered_by=triggered_by
            )
            return rid
        return _run_contracts(d, agent, org_id=org_id, trigger=trigger, triggered_by=triggered_by)


def _last_cursor(
    s: Session, org_id: uuid.UUID, agent_key: str, this_run: uuid.UUID
) -> uuid.UUID | None:
    prev = s.scalars(
        select(AgentRun)
        .where(
            AgentRun.organization_id == org_id,
            AgentRun.agent_key == agent_key,
            AgentRun.contract_id.is_(None),
            AgentRun.id != this_run,
            AgentRun.status != AgentRunStatus.koerer,
        )
        .order_by(AgentRun.started_at.desc())
        .limit(1)
    ).first()
    raw = (prev.error_context or {}).get("cursor") if prev is not None else None
    return uuid.UUID(raw) if raw else None


def _run_contracts(
    d: AgentDefinition,
    agent: Any,
    *,
    org_id: uuid.UUID,
    trigger: AgentTrigger,
    triggered_by: uuid.UUID | None,
) -> uuid.UUID:
    spec: runtime.AgentSpec = agent.SPEC
    execute: runtime.Execute = agent.EXECUTE
    doc_types = agent.DOC_TYPES
    cap = settings.agent_contracts_per_run
    started = datetime.now(UTC)
    with tenant(org_id, system=True), SessionLocal() as s:
        run = AgentRun(
            organization_id=org_id,
            agent_key=spec.key,
            contract_id=None,
            trigger=trigger,
            triggered_by=triggered_by,
            task=spec.task,
            started_at=started,
        )
        s.add(run)
        s.commit()
        run_id = run.id
        if runtime.is_disabled(s, org_id, spec.key):
            run.status = AgentRunStatus.sprunget_over
            run.error_context = {"reason": "disabled"}
            runtime.finish(run, started)
            s.commit()
            return run_id

        cursor = _last_cursor(s, org_id, spec.key, run_id)
        q = (
            select(Contract.id)
            .where(Contract.status.in_(MONITORED))
            .order_by(Contract.id)
            .limit(cap + 1)
        )
        if cursor is not None:
            q = q.where(Contract.id > cursor)
        ids = list(s.scalars(q).all())
        more = len(ids) > cap
        ids = ids[:cap]

        # A carrier for the per-contract counters and token usage: the agents write
        # to `run.suggestions_created` etc. and link proposals to `run.id`. It is
        # never added to the session, so a rollback cannot touch the tallies.
        carrier = AgentRun(
            id=run_id,
            organization_id=org_id,
            agent_key=spec.key,
            trigger=trigger,
            task=spec.task,
            started_at=started,
        )
        scanned = created = updated = skipped = 0
        failures: list[dict[str, str]] = []
        last_id: uuid.UUID | None = None
        budget_hit: str | None = None
        for cid in ids:
            carrier.suggestions_created = 0
            carrier.suggestions_updated = 0
            try:
                out = runtime.execute_one(s, carrier, spec, execute, cid, doc_types)
                s.commit()
            except llm.LlmBudgetExceeded as e:
                s.rollback()
                budget_hit = str(e)
                break  # this contract is retried next time: the cursor stays before it
            except Exception as e:  # noqa: BLE001 — one contract never stops the others
                s.rollback()
                log.exception("agent=%s org=%s contract=%s failed", spec.key, org_id, cid)
                failures.append(
                    {"contract_id": str(cid), "error": f"{e.__class__.__name__}: {e}"[:300]}
                )
                scanned += 1
                last_id = cid
                continue
            scanned += 1
            last_id = cid
            if out.status == AgentRunStatus.ok:
                created += carrier.suggestions_created
                updated += carrier.suggestions_updated
            else:
                skipped += 1
            # progress survives a crash mid-night
            run.contracts_scanned = scanned
            run.suggestions_created = created
            run.suggestions_updated = updated
            s.commit()

        run.contracts_scanned = scanned
        run.suggestions_created = created
        run.suggestions_updated = updated
        run.model = carrier.model
        run.input_tokens = carrier.input_tokens
        run.output_tokens = carrier.output_tokens
        run.cost_dkk = carrier.cost_dkk
        ctx: dict[str, Any] = {"skipped": skipped, "failed": failures}
        next_cursor = last_id or cursor
        if budget_hit is not None:
            run.status = AgentRunStatus.sprunget_over
            run.error = budget_hit
            ctx["reason"] = "budget"
            if next_cursor is not None:
                ctx["cursor"] = str(next_cursor)
        elif scanned and len(failures) == scanned:
            run.status = AgentRunStatus.fejlet
            run.error = f"Alle {scanned} kontrakter fejlede"
        else:
            run.status = AgentRunStatus.ok
            if more and last_id is not None:
                ctx["reason"] = "cap"
                ctx["cursor"] = str(last_id)
        run.error_context = ctx
        if run.status == AgentRunStatus.ok:
            runtime.audit_completed(s, run, spec, None)
        runtime.finish(run, started)
        s.commit()
        log.info(
            "agent=%s org=%s run=%s status=%s scanned=%d created=%d updated=%d failed=%d",
            spec.key,
            org_id,
            run_id,
            run.status,
            scanned,
            created,
            updated,
            len(failures),
        )
        return run_id
