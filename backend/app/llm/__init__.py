"""The ONLY module allowed to import `anthropic` (ADR-0008, gate G-04).

Entry point (to come): run(task, context) -> ValidatedResult. Callers name a task
from app/llm/config.py; they never name a model. Every call sets tenant context,
writes an audit row, meters usage (ADR-0014) and validates the response schema
before anything becomes an ai_suggestions row (ADR-0004).

Sampling params (temperature/top_p/top_k/budget_tokens) do not exist on current
models and are rejected by gate G-14.
"""
