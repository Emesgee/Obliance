"""Agents — each writes proposals only (ADR-0004) and runs as a job (ADR-0010).

`register()` wires the event listeners; main.py calls it once at startup so a
document version switch (ADR-0006) expires stale suggestions and kicks off the
Contract Intake Agent for agreement documents.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.agents import contract_intake, obligation_extract, risk_assess
from app.ai import resolution, suggestions
from app.core import events, jobs
from app.core.db import SessionLocal
from app.core.rls import tenant
from app.domain.models import AGREEMENT_DOC_TYPES, AgentTrigger, DocType

AGENTS: dict[str, Any] = {
    contract_intake.AGENT_KEY: contract_intake,
    obligation_extract.AGENT_KEY: obligation_extract,
    risk_assess.AGENT_KEY: risk_assess,
}
# The document-driven agents, in the order they run on a version switch (ADR-0006 §4).
ON_VERSION_SWITCH = (contract_intake, obligation_extract, risk_assess)


def _on_version_changed(
    *,
    organization_id: uuid.UUID,
    contract_id: uuid.UUID,
    document_id: uuid.UUID,
    old_version_id: uuid.UUID | None,
    new_version_id: uuid.UUID,
    doc_type: DocType,
    **_: Any,
) -> None:
    # 1. expire what the switch invalidated (ADR-0004 §2) and re-resolve the
    #    register's citations against the new version (ADR-0005 §5) — system
    #    context, own transaction
    with tenant(organization_id, system=True), SessionLocal() as s:
        suggestions.expire_for_version(
            s, org_id=organization_id, contract_id=contract_id, old_version_id=old_version_id
        )
        resolution.reresolve_version(
            s,
            org_id=organization_id,
            contract_id=contract_id,
            old_version_id=old_version_id,
            new_version_id=new_version_id,
        )
        s.commit()
    # 2. re-read the agreement (ADR-0006 §4): intake, obligations, risks. RACI later.
    if doc_type in AGREEMENT_DOC_TYPES:
        for agent in ON_VERSION_SWITCH:
            jobs.enqueue(
                agent.run_for_contract,
                org_id=organization_id,
                contract_id=contract_id,
                trigger=AgentTrigger.event,
                trigger_ref=str(new_version_id),
            )


_registered = False


def register() -> None:
    global _registered
    if _registered:
        return
    events.subscribe(events.DOCUMENT_VERSION_CHANGED, _on_version_changed)
    _registered = True
