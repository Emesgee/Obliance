"""`run(...)` — the one door to a model (ADR-0008 §1/§2, ADR-0009 §1/§3).

Four things no call site can skip, in this order:

  1. a tenant context must be active (ADR-0002) — no context, no call
  2. the daily budget is checked (ADR-0010 §7) — a hard stop, before spending
  3. an audit row "ai_query" is committed BEFORE the call (ADR-0011: it matters
     more to know that someone asked than that they got an answer)
  4. stop_reason is checked (refusal, max_tokens) and the JSON is validated
     against the task's pydantic schema — an answer that does not validate is
     not a result; usage is recorded either way (ADR-0014)
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.core import audit, rls
from app.core.config import settings
from app.domain.models import ActorType, AuditAction
from app.llm import config, usage
from app.llm.context import INJECTION_RULE, DataBlock, pack
from app.llm.provider import (
    LlmError,
    Provider,
    ProviderRequest,
    ProviderUsage,
    current,
)

log = logging.getLogger(__name__)


class LlmContextError(LlmError):
    code = "llm_no_tenant_context"


class LlmBudgetExceeded(LlmError):
    code = "llm_budget_exceeded"


class LlmRefused(LlmError):
    code = "llm_refused"


class LlmTruncated(LlmError):
    code = "llm_truncated"


class LlmInvalidOutput(LlmError):
    code = "llm_invalid_output"


@dataclass(frozen=True, slots=True)
class LlmResult[T: BaseModel]:
    data: T
    task: str
    model: str
    backend: str
    usage: ProviderUsage
    cost_dkk: Decimal | None
    stop_reason: str


def _active_context(session: Session) -> rls.TenantContext:
    ctx = session.info.get(rls.SESSION_KEY) or rls.current()
    if ctx is None:
        raise LlmContextError("app/llm kaldt uden tenant-kontekst (ADR-0002/0008)")
    return ctx


def run[T: BaseModel](
    session: Session,
    task: str,
    *,
    schema: type[T],
    instructions: str,
    material: Sequence[DataBlock],
    question: str,
    org_id: uuid.UUID,
    actor: audit.Actor,
    contract_id: uuid.UUID | None = None,
    contract_label: str = "",
    agent_run_id: uuid.UUID | None = None,
    provider: Provider | None = None,
) -> LlmResult[T]:
    _active_context(session)
    cfg = config.resolve(task)

    spent = usage.spent_today_dkk(session, org_id)
    if spent >= settings.llm_daily_budget_dkk:
        raise LlmBudgetExceeded(
            f"Døgnbudgettet på {settings.llm_daily_budget_dkk} DKK er brugt ({spent:.2f} DKK)"
        )

    audit.record(
        session,
        org_id=org_id,
        action=AuditAction.ai_query,
        actor=actor,
        object_kind="contract" if contract_id else "organization",
        object_id=contract_id or org_id,
        object_label=contract_label,
        contract_id=contract_id,
        details={"task": task},
        agent_run_id=agent_run_id,
    )
    session.commit()

    prov = provider or current()
    req = ProviderRequest(
        model=cfg.model,
        effort=cfg.effort,
        max_tokens=cfg.max_tokens,
        system=f"{instructions.strip()}\n\n{INJECTION_RULE}",
        material=pack(material),
        question=question,
        output_schema=schema.model_json_schema(),
    )
    # Operational log: task, ids and sizes only — never prompt content (ADR-0016 §5).
    log.info(
        "llm task=%s model=%s org=%s contract=%s material_chars=%d",
        task,
        cfg.model,
        org_id,
        contract_id,
        len(req.material),
    )
    resp = prov.complete(req)

    actor_type = ActorType.human if actor.type == ActorType.human else ActorType.agent
    row = usage.record(
        session,
        org_id=org_id,
        task=task,
        actor_type=actor_type,
        model=resp.model,
        backend=prov.name,
        usage=resp.usage,
        user_id=actor.id if actor.type == ActorType.human else None,
        contract_id=contract_id,
        agent_run_id=agent_run_id,
    )
    cost = row.cost_dkk if row is not None else None

    if resp.stop_reason == "refusal":
        raise LlmRefused("Modellen afviste forespørgslen (stop_reason=refusal)")
    if resp.stop_reason == "max_tokens":
        raise LlmTruncated("Svaret blev afbrudt ved max_tokens — skemaet er ufuldstændigt")
    try:
        data = schema.model_validate(json.loads(resp.text))
    except (json.JSONDecodeError, ValidationError) as e:
        raise LlmInvalidOutput(f"Svaret validerer ikke mod {schema.__name__}: {e}") from e

    return LlmResult(
        data=data,
        task=task,
        model=resp.model,
        backend=prov.name,
        usage=resp.usage,
        cost_dkk=cost,
        stop_reason=resp.stop_reason,
    )
