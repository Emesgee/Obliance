"""Agent definitions — code, not DB (ADR-0010 §1).

    key, label, purpose, cadence (cron, Europe/Copenhagen), task (ADR-0009),
    scope (contract | org), trigger (schedule | event | both)

The scheduler reads cadences from here and enqueues one job per (agent, org).
`agent_settings.schedule_override` replaces the cadence for one organisation;
`enabled = false` pauses the agent there (ADR-0010 §2). Cadences are spread so
the document-driven agents run first and the rule agents afterwards, on the
proposals the night produced. Nothing runs every minute (ADR-0010 §1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Scope = Literal["contract", "org"]
Trigger = Literal["schedule", "event", "both"]


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    key: str
    label: str
    purpose: str
    task: str | None  # ADR-0009 task; None = rule agent, no model (ADR-0009 §1)
    scope: Scope
    trigger: Trigger
    cadence: str | None  # 5-field cron in Europe/Copenhagen; None = event/manual only
    event: str | None = None  # what fires the event-driven run

    @property
    def scheduled(self) -> bool:
        return self.cadence is not None and self.trigger in ("schedule", "both")


DEFINITIONS: tuple[AgentDefinition, ...] = (
    AgentDefinition(
        key="contract_intake",
        label="Contract Intake Agent",
        purpose="Læser stamdata ud af aftalegrundlaget ved upload.",
        task="contract_intake",
        scope="contract",
        trigger="event",
        cadence=None,
        event="upload af aftalegrundlag",
    ),
    AgentDefinition(
        key="obligation_extract",
        label="Obligation Extraction Agent",
        purpose="Finder forpligtelser, KPI'er, pris- og bodsvilkår i aftalegrundlaget.",
        task="obligation_extract",
        scope="contract",
        trigger="both",
        cadence="0 2 * * *",
        event="ny dokumentversion",
    ),
    AgentDefinition(
        key="risk_assess",
        label="Risk Agent",
        purpose="Vurderer risici i aftalegrundlaget og på registret.",
        task="risk_assess",
        scope="contract",
        trigger="both",
        cadence="20 2 * * *",
        event="ny dokumentversion",
    ),
    AgentDefinition(
        key="raci_design",
        label="RACI Design Agent",
        purpose="Foreslår ansvarsmatrix fra klausuler og skabeloner.",
        task="raci_design",
        scope="contract",
        trigger="both",
        cadence="40 2 * * *",
        event="ny dokumentversion",
    ),
    AgentDefinition(
        key="kpi_parse",
        label="KPI/SLA Agent",
        purpose="Læser KPI-målinger ud af leverandørrapporter.",
        task="kpi_parse",
        scope="contract",
        trigger="event",
        cadence=None,
        event="upload af rapport",
    ),
    AgentDefinition(
        key="responsibility_gap",
        label="Responsibility Gap Agent",
        purpose="Finder huller i ansvarsfordelingen (regler G1–G7).",
        task=None,
        scope="org",
        trigger="schedule",
        cadence="0 5 * * *",
    ),
    AgentDefinition(
        key="workload_capacity",
        label="Workload & Capacity Agent",
        purpose="Vurderer belastning pr. medarbejder mod politikken.",
        task=None,
        scope="org",
        trigger="schedule",
        cadence="10 5 * * *",
    ),
)

BY_KEY: dict[str, AgentDefinition] = {d.key: d for d in DEFINITIONS}

# ADR-0010 §7: alerts are evaluated once a day, after the night's runs.
ALERT_CRON = "0 7 * * *"
