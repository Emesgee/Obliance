"""Agent definitions, scheduler and rule engines (ADR-0010, ADR-0021).

Agents write ONLY to ai_suggestions and agent_runs (ADR-0004, gate G-07). The
worker role has SELECT on the registers and nothing else.
"""
