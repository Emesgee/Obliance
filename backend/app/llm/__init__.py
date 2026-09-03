"""app/llm — the only module that talks to a model provider (ADR-0008 §1).

from app import llm
result = llm.run(session, "contract_intake", schema=..., instructions=..., material=[...], ...)
"""

from app.llm.client import (
    LlmBudgetExceeded,
    LlmContextError,
    LlmInvalidOutput,
    LlmRefused,
    LlmResult,
    LlmTruncated,
    run,
)
from app.llm.context import DataBlock, PageBlock
from app.llm.provider import LlmError, LlmNotConfigured, LlmProviderError

__all__ = [
    "DataBlock",
    "LlmBudgetExceeded",
    "LlmContextError",
    "LlmError",
    "LlmInvalidOutput",
    "LlmNotConfigured",
    "LlmProviderError",
    "LlmRefused",
    "LlmResult",
    "LlmTruncated",
    "PageBlock",
    "run",
]
