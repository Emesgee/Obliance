"""Usage measurement — ADR-0014. One row per operation, price frozen at write.
Best-effort on write (never fails the call); the budget check is NOT best-effort
and runs before the call (ADR-0010 §7)."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domain.models import ActorType, UsageEvent
from app.llm import config
from app.llm.provider import ProviderUsage

log = logging.getLogger(__name__)

_M = Decimal(1_000_000)


def cost_usd(model: str, u: ProviderUsage, *, batch: bool = False) -> Decimal | None:
    p = config.price_for(model)
    if p is None:
        return None
    usd = (
        Decimal(u.input_tokens) * p.input_per_m
        + Decimal(u.output_tokens) * p.output_per_m
        + Decimal(u.cache_read_tokens) * p.input_per_m * p.cache_read_factor
        + Decimal(u.cache_write_tokens) * p.input_per_m * p.cache_write_factor
    ) / _M
    if batch:
        usd *= config.BATCH_FACTOR
    if u.inference_geo == "us":
        usd *= config.US_GEO_FACTOR
    return usd.quantize(Decimal("0.000001"))


def record(
    session: Session,
    *,
    org_id: uuid.UUID,
    task: str,
    actor_type: ActorType,
    model: str,
    backend: str,
    usage: ProviderUsage,
    user_id: uuid.UUID | None = None,
    contract_id: uuid.UUID | None = None,
    agent_run_id: uuid.UUID | None = None,
    batch: bool = False,
) -> UsageEvent | None:
    """Write the row; swallow and log any failure (ADR-0014 §3)."""
    try:
        usd = cost_usd(model, usage, batch=batch)
        rate = settings.dkk_per_usd
        row = UsageEvent(
            organization_id=org_id,
            occurred_at=datetime.now(UTC),
            task=task,
            actor_type=actor_type,
            user_id=user_id,
            contract_id=contract_id,
            agent_run_id=agent_run_id,
            model=model,
            backend=backend,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            batch=batch,
            inference_geo=usage.inference_geo,
            cost_usd=usd,
            cost_dkk=(usd * rate).quantize(Decimal("0.0001")) if usd is not None else None,
            dkk_per_usd=rate,
        )
        session.add(row)
        session.flush()
        return row
    except Exception:
        log.exception("usage_events: could not record task=%s org=%s", task, org_id)
        return None


def spent_today_dkk(session: Session, org_id: uuid.UUID) -> Decimal:
    since = datetime.now(UTC) - timedelta(days=1)
    total = session.scalar(
        select(func.coalesce(func.sum(UsageEvent.cost_dkk), 0)).where(
            UsageEvent.organization_id == org_id, UsageEvent.occurred_at >= since
        )
    )
    return Decimal(total or 0)
