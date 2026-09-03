"""Audit log writer — ADR-0011.

    audit.record(session, org_id=..., action=AuditAction.x, actor=audit.human(principal),
                 object_kind="contract", object_id=c.id, object_label=c.reference,
                 contract_id=c.id, details={...})

Three actor kinds, labels frozen at write time, a hash chain filled from day one
(§6: verification tooling comes later; the chain cannot be applied backwards).
Written in the caller's transaction so an action cannot succeed without a trace —
except ADR-0008's ai_query, which the LLM layer commits *before* the call.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import ActorType, AuditAction, AuditLog

if TYPE_CHECKING:
    from app.core.auth import Principal


@dataclass(frozen=True, slots=True)
class Actor:
    type: ActorType
    label: str
    id: uuid.UUID | None = None
    role: str | None = None


def human(principal: Principal) -> Actor:
    return Actor(ActorType.human, principal.name, principal.user_id, principal.role.value)


def agent(label: str) -> Actor:
    """e.g. 'AI · Contract Intake Agent' — the mockup's spelling."""
    return Actor(ActorType.agent, label)


def system(label: str) -> Actor:
    return Actor(ActorType.system, label)


def _row_hash(prev: str | None, fields: dict[str, Any]) -> str:
    canonical = json.dumps(fields, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(((prev or "") + "\n" + canonical).encode("utf-8")).hexdigest()


def record(
    session: Session,
    *,
    org_id: uuid.UUID,
    action: AuditAction,
    actor: Actor,
    object_kind: str,
    object_id: uuid.UUID | None = None,
    object_label: str = "",
    contract_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
    agent_run_id: uuid.UUID | None = None,
    request_id: str | None = None,
) -> AuditLog:
    now = datetime.now(UTC)
    prev = session.scalar(
        select(AuditLog.row_hash)
        .where(AuditLog.organization_id == org_id)
        .order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
        .limit(1)
    )
    fields: dict[str, Any] = {
        "organization_id": org_id,
        "occurred_at": now.isoformat(),
        "actor_type": actor.type.value,
        "actor_id": actor.id,
        "actor_label": actor.label,
        "actor_role": actor.role,
        "action": action.value,
        "object_kind": object_kind,
        "object_id": object_id,
        "object_label": object_label,
        "contract_id": contract_id,
        "details": details or {},
        "agent_run_id": agent_run_id,
    }
    row = AuditLog(
        organization_id=org_id,
        occurred_at=now,
        actor_type=actor.type,
        actor_id=actor.id,
        actor_label=actor.label,
        actor_role=actor.role,
        action=action,
        object_kind=object_kind,
        object_id=object_id,
        object_label=object_label,
        contract_id=contract_id,
        details=details or {},
        agent_run_id=agent_run_id,
        request_id=request_id,
        prev_hash=prev,
        row_hash=_row_hash(prev, fields),
    )
    session.add(row)
    session.flush()
    return row
