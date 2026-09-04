"""The rule agents — Responsibility Gap and Workload & Capacity (ADR-0021 §3/§5,
ADR-0009 §1: no model). Same run trail (agent_runs), same proposal flow
(ADR-0004), audit actor `System · …` (ADR-0009 afklaring 3). Org-scoped: one run
per organisation, optionally narrowed to one contract for the manual button.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from app.core import audit
from app.core.db import SessionLocal
from app.core.rls import tenant
from app.domain.models import AgentRun, AgentRunStatus, AgentSetting, AgentTrigger, AuditAction
from app.raci import service

log = logging.getLogger(__name__)


def _run(
    key: str,
    label: str,
    *,
    org_id: uuid.UUID,
    contract_id: uuid.UUID | None,
    trigger: AgentTrigger,
    trigger_ref: str | None,
    triggered_by: uuid.UUID | None,
) -> uuid.UUID:
    started = datetime.now(UTC)
    with tenant(org_id, system=True), SessionLocal() as s:
        run = AgentRun(
            organization_id=org_id,
            agent_key=key,
            contract_id=contract_id,
            trigger=trigger,
            trigger_ref=trigger_ref,
            triggered_by=triggered_by,
            task=None,  # no model (ADR-0009 §1)
            started_at=started,
        )
        s.add(run)
        s.commit()
        try:
            setting = s.get(AgentSetting, (org_id, key))
            if setting is not None and not setting.enabled:
                run.status = AgentRunStatus.sprunget_over
                run.error_context = {"reason": "disabled"}
            else:
                scope = [contract_id] if contract_id else None
                if key == GAP_KEY:
                    findings = service.find_gaps(s, org_id, scope)
                else:
                    findings = service.workload_findings(s, org_id)
                    if scope:
                        findings = [f for f in findings if f.contract_id in scope]
                run.contracts_scanned = (
                    len({f.contract_id for f in findings}) if scope is None else 1
                )
                c, u, closed = service.write_findings(
                    s,
                    org_id=org_id,
                    agent_key=key,
                    label=label,
                    findings=findings,
                    agent_run_id=run.id,
                    scope_contract_ids=scope,
                )
                run.suggestions_created = c
                run.suggestions_updated = u
                run.error_context = {"closed": closed, "rules": sorted({f.rule for f in findings})}
                run.status = AgentRunStatus.ok
                audit.record(
                    s,
                    org_id=org_id,
                    action=AuditAction.agent_run_completed,
                    actor=audit.system(label),
                    object_kind="agent_run",
                    object_id=run.id,
                    contract_id=contract_id,
                    details={"created": c, "updated": u, "closed": closed},
                    agent_run_id=run.id,
                )
        except Exception as e:  # noqa: BLE001
            log.exception("%s failed", key)
            run.status = AgentRunStatus.fejlet
            run.error = f"{e.__class__.__name__}: {e}"[:1000]
        finally:
            finished = datetime.now(UTC)
            run.finished_at = finished
            run.duration_ms = int((finished - started).total_seconds() * 1000)
            s.commit()
        return run.id


GAP_KEY = "responsibility_gap"
GAP_LABEL = service.GAP_LABEL
WORKLOAD_KEY = "workload_capacity"
WORKLOAD_LABEL = service.WORKLOAD_LABEL


class _Agent:
    def __init__(self, key: str, label: str) -> None:
        self.AGENT_KEY = key
        self.LABEL = label

    def run_for_contract(
        self,
        *,
        org_id: uuid.UUID,
        contract_id: uuid.UUID | None = None,
        trigger: AgentTrigger,
        trigger_ref: str | None = None,
        triggered_by: uuid.UUID | None = None,
    ) -> uuid.UUID:
        return _run(
            self.AGENT_KEY,
            self.LABEL,
            org_id=org_id,
            contract_id=contract_id,
            trigger=trigger,
            trigger_ref=trigger_ref,
            triggered_by=triggered_by,
        )

    def run_for_org(
        self, *, org_id: uuid.UUID, trigger: AgentTrigger, triggered_by: uuid.UUID | None = None
    ) -> uuid.UUID:
        return self.run_for_contract(
            org_id=org_id, contract_id=None, trigger=trigger, triggered_by=triggered_by
        )


responsibility_gap = _Agent(GAP_KEY, GAP_LABEL)
workload_capacity = _Agent(WORKLOAD_KEY, WORKLOAD_LABEL)
