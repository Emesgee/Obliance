"""Materialiser for `task` proposals (gaps, workload, later meeting prep): the
human's approval creates the tasks row; a chosen candidate becomes responsible."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.ai import suggestions
from app.core import audit
from app.core.auth import Principal
from app.domain.models import AiSuggestion, Origin, SuggestionSubject
from app.raci import service


def materialize(
    session: Session, s: AiSuggestion, principal: Principal
) -> suggestions.Materialized:
    p = s.payload
    responsible = p.get("responsible_id") or p.get("candidate_id")
    t = service.create_task(
        session,
        org_id=s.organization_id,
        contract_id=s.contract_id,
        title=str(p.get("title") or "Opgave"),
        description=p.get("description"),
        responsible_id=uuid.UUID(str(responsible)) if responsible else None,
        deadline=None,
        priority=str(p.get("priority") or "mellem"),
        origin=Origin.ai,
        origin_kind=f"gap:{p['rule']}" if p.get("rule") else "suggestion",
        origin_ref=p.get("object_ref"),
        actor=audit.human(principal),
        actor_id=principal.user_id,
        suggestion_id=s.id,
    )
    return suggestions.Materialized(materialized_id=t.id, applied=["task"])


suggestions.MATERIALIZERS[SuggestionSubject.task] = materialize
