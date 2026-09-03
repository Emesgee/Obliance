"""Task → model, effort and price. THE single source (ADR-0009 §1, ADR-0014 §2).

Gate G-04 fails the build if a model id appears anywhere else under app/. Call
sites name a task, never a model; `LLM_MODEL_<TASK>` in the environment overrides
one task for debugging or a pilot measurement.

Prices are USD per million tokens at decision time (ADR-0009/0014, 2026-09-03).
A price change is a dated config change; stored rows keep the price they were
written with.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

Effort = Literal["low", "medium", "high", "xhigh", "max"]

OPUS = "claude-opus-5"
SONNET = "claude-sonnet-5"
HAIKU = "claude-haiku-4-5-20251001"


@dataclass(frozen=True, slots=True)
class TaskConfig:
    task: str
    model: str
    effort: Effort
    max_tokens: int


@dataclass(frozen=True, slots=True)
class Price:
    input_per_m: Decimal
    output_per_m: Decimal
    # Anthropic list: cache reads 0.1×, 5-minute cache writes 1.25× the input price.
    cache_read_factor: Decimal = Decimal("0.1")
    cache_write_factor: Decimal = Decimal("1.25")


# ADR-0009 §1 — the table. Three agents (renewal_scan, responsibility_gap,
# workload_capacity) use no model on purpose and are therefore absent here.
TASKS: dict[str, TaskConfig] = {
    t.task: t
    for t in (
        TaskConfig("obligation_extract", OPUS, "high", 16000),
        TaskConfig("raci_design", OPUS, "high", 8000),
        TaskConfig("risk_assess", OPUS, "medium", 8000),
        TaskConfig("contract_intake", OPUS, "medium", 6000),
        TaskConfig("copilot", OPUS, "medium", 4000),
        TaskConfig("invoice_check", SONNET, "medium", 8000),
        TaskConfig("meeting_prep", SONNET, "low", 3000),
        TaskConfig("draft_letter", SONNET, "medium", 3000),
        TaskConfig("ocr_page", SONNET, "low", 4000),
        TaskConfig("cert_extract", HAIKU, "low", 1000),
        TaskConfig("kpi_parse", HAIKU, "low", 3000),
        TaskConfig("supplier_summary", HAIKU, "low", 2000),
    )
}

PRICES: dict[str, Price] = {
    OPUS: Price(Decimal("5"), Decimal("25")),
    SONNET: Price(Decimal("2"), Decimal("10")),
    HAIKU: Price(Decimal("1"), Decimal("5")),
}

BATCH_FACTOR = Decimal("0.5")  # ADR-0009 §4
US_GEO_FACTOR = Decimal("1.1")  # ADR-0008 §3: inference_geo "us" pin costs 1.1×


class UnknownTask(KeyError):
    pass


def resolve(task: str) -> TaskConfig:
    try:
        cfg = TASKS[task]
    except KeyError as e:
        raise UnknownTask(task) from e
    override = os.environ.get(f"LLM_MODEL_{task.upper()}")
    if override:
        return TaskConfig(cfg.task, override, cfg.effort, cfg.max_tokens)
    return cfg


def price_for(model: str) -> Price | None:
    """None for an unknown model: the usage row is still written, with cost null
    (ADR-0014 §3 — measurement never fails the call)."""
    return PRICES.get(model)
