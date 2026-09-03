"""Agents — each writes proposals only (ADR-0004) and runs as a job (ADR-0010).

`register()` wires the event listeners; main.py calls it once at startup so a
document version switch (ADR-0006) expires stale suggestions and kicks off the
Contract Intake Agent for agreement documents.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.agents import contract_intake
from app.ai import suggestions
from app.core import events, jobs
from app.core.db import SessionLocal
from app.core.rls import tenant
from app.domain.models import AGREEMENT_DOC_TYPES, AgentTrigger, DocType

AGENTS: dict[str, Any] = {contract_intake.AGENT_KEY: contract_intake}


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
    # 1. expire what the switch invalidated (ADR-0004 §2) — system context, own tx
    with tenant(organization_id, system=True), SessionLocal() as s:
        suggestions.expire_for_version(
            s, org_id=organization_id, contract_id=contract_id, old_version_id=old_version_id
        )
        s.commit()
    # 2. re-read the agreement (ADR-0006 §4 — intake for now; obligations/risks later)
    if doc_type in AGREEMENT_DOC_TYPES:
        jobs.enqueue(
            contract_intake.run_for_contract,
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
