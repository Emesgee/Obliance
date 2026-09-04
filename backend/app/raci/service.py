"""RACI services — ADR-0021 §1 validation, §2 staffing, §3 gap rules, §5 workload.

Everything here is queries and rules; no model. Gap findings and workload
findings are proposals of subject `task` (ADR-0004) written by a system actor
and closed automatically when the gap is gone.
"""

# ruff: noqa: E501  — finding texts read better unwrapped
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import suggestions
from app.core import audit
from app.domain.models import (
    OPEN_SUGGESTION_STATUSES,
    AiSuggestion,
    AuditAction,
    Confidence,
    Confidentiality,
    Contract,
    ContractAccess,
    ContractPhase,
    ContractRole,
    ContractStatus,
    Criticality,
    Obligation,
    ObligationStatus,
    Profile,
    RaciActivity,
    RaciAssignment,
    RaciFunction,
    RaciLetter,
    RaciStatus,
    RaciTemplate,
    Risk,
    RiskStatus,
    SuggestionKind,
    SuggestionStatus,
    SuggestionSubject,
    Task,
    TaskStatus,
    WorkloadPolicy,
)

GAP_LABEL = "System · Responsibility Gap"
WORKLOAD_LABEL = "System · Workload & Capacity"
FUNCTION_NAMES = {
    "CM": "Contract Manager",
    "CO": "Contract Owner",
    "PROC": "Procurement",
    "LEGAL": "Legal & Compliance",
    "FIN": "Finance Controller",
    "IT": "IT",
    "BUS": "Forretning",
    "LEV": "Leverandør",
}


class RaciError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


# ---- §1 validation ---------------------------------------------------------------------------


def validate(cells: dict[str, str]) -> list[str]:
    """cells = {function: letter}. Returns the rule violations (empty = valid)."""
    errors: list[str] = []
    letters = list(cells.values())
    a_count = letters.count("A")
    if a_count == 0:
        errors.append("Præcis ét A pr. aktivitet — der er intet A")
    elif a_count > 1:
        errors.append(f"Præcis ét A pr. aktivitet — der er {a_count}")
    if "R" not in letters:
        errors.append("Mindst ét R")
    if cells.get("LEV") == "A":
        errors.append(
            "LEV kan ikke være A — leverandøren er ikke ansvarlig for kundens beslutninger"
        )
    for fn, letter in cells.items():
        if fn not in RaciFunction.__members__:
            errors.append(f"Ukendt funktion {fn}")
        if letter not in RaciLetter.__members__:
            errors.append(f"Ukendt bogstav {letter}")
    return errors


def cells_of(session: Session, activity_id: uuid.UUID) -> dict[str, str]:
    rows = session.scalars(
        select(RaciAssignment).where(RaciAssignment.activity_id == activity_id)
    ).all()
    return {r.function.value: r.letter.value for r in rows}


def next_seq(session: Session, contract_id: uuid.UUID) -> int:
    return (
        int(
            session.scalar(
                select(func.coalesce(func.max(RaciActivity.seq), 0)).where(
                    RaciActivity.contract_id == contract_id
                )
            )
            or 0
        )
        + 1
    )


def create_activity(
    session: Session,
    *,
    contract: Contract,
    name: str,
    criticality: Criticality,
    cells: dict[str, str],
    actor: audit.Actor,
    actor_id: uuid.UUID | None,
    origin: Any,
    template_key: str | None = None,
    suggestion_id: uuid.UUID | None = None,
) -> RaciActivity:
    errors = validate(cells)
    if errors:
        raise RaciError("invalid_matrix", "; ".join(errors), 409)
    now = datetime.now(UTC)
    act = RaciActivity(
        organization_id=contract.organization_id,
        contract_id=contract.id,
        seq=next_seq(session, contract.id),
        name=name.strip()[:200],
        criticality=criticality,
        status=RaciStatus.godkendt,
        template_key=template_key,
        origin=origin,
        suggestion_id=suggestion_id,
        created_by=actor_id,
        created_at=now,
        updated_at=now,
    )
    session.add(act)
    session.flush()
    for fn, letter in cells.items():
        session.add(
            RaciAssignment(
                organization_id=contract.organization_id,
                contract_id=contract.id,
                activity_id=act.id,
                function=RaciFunction(fn),
                letter=RaciLetter(letter),
            )
        )
    session.flush()
    audit.record(
        session,
        org_id=contract.organization_id,
        action=AuditAction.raci_activity_created,
        actor=actor,
        object_kind="raci_activity",
        object_id=act.id,
        object_label=f"RA-{act.seq} {act.name}",
        contract_id=contract.id,
        details={"cells": cells, "origin": str(getattr(origin, "value", origin))},
    )
    return act


def set_cell(
    session: Session,
    *,
    activity: RaciActivity,
    function: str,
    letter: str | None,
    actor: audit.Actor,
) -> dict[str, str]:
    """Change one cell; the whole row must stay valid (ADR-0021 afklaring 1)."""
    cells = cells_of(session, activity.id)
    before = cells.get(function)
    if letter is None:
        cells.pop(function, None)
    else:
        cells[function] = letter
    errors = validate(cells)
    if errors:
        raise RaciError("invalid_matrix", "; ".join(errors), 409)
    row = session.scalars(
        select(RaciAssignment).where(
            RaciAssignment.activity_id == activity.id,
            RaciAssignment.function == RaciFunction(function),
        )
    ).first()
    if letter is None:
        if row is not None:
            session.delete(row)
    elif row is None:
        session.add(
            RaciAssignment(
                organization_id=activity.organization_id,
                contract_id=activity.contract_id,
                activity_id=activity.id,
                function=RaciFunction(function),
                letter=RaciLetter(letter),
            )
        )
    else:
        row.letter = RaciLetter(letter)
    activity.updated_at = datetime.now(UTC)
    session.flush()
    audit.record(
        session,
        org_id=activity.organization_id,
        action=AuditAction.raci_cell_changed,
        actor=actor,
        object_kind="raci_activity",
        object_id=activity.id,
        object_label=f"RA-{activity.seq} {activity.name}",
        contract_id=activity.contract_id,
        details={"function": function, "before": before, "after": letter},
    )
    return cells


# ---- §2 staffing ------------------------------------------------------------------------------


def active_roles(session: Session, contract_id: uuid.UUID) -> dict[str, ContractRole]:
    rows = session.scalars(
        select(ContractRole).where(
            ContractRole.contract_id == contract_id, ContractRole.until.is_(None)
        )
    ).all()
    return {r.function.value: r for r in rows}


def assign_role(
    session: Session,
    *,
    contract: Contract,
    function: RaciFunction,
    profile_id: uuid.UUID | None,
    supplier_contact: str | None,
    actor: audit.Actor,
    actor_id: uuid.UUID | None,
) -> ContractRole | None:
    """Close the current holder (until = today) and open the new one; mirror CO/CM
    onto contracts.owner_id/manager_id (one truth, two read paths)."""
    if function == RaciFunction.LEV and profile_id is not None:
        raise RaciError("lev_is_not_a_user", "LEV peger på en leverandørkontakt, ikke en bruger")
    if function != RaciFunction.LEV and supplier_contact:
        raise RaciError("contact_only_for_lev", "Kun LEV har en leverandørkontakt")
    today = date.today()
    current = active_roles(session, contract.id).get(function.value)
    before = (
        str(current.profile_id)
        if current and current.profile_id
        else (current.supplier_contact if current else None)
    )
    if current is not None:
        current.until = today
        session.flush()
    row: ContractRole | None = None
    if profile_id is not None or supplier_contact:
        row = ContractRole(
            organization_id=contract.organization_id,
            contract_id=contract.id,
            function=function,
            profile_id=profile_id,
            supplier_contact=supplier_contact,
            since=today,
            assigned_by=actor_id,
        )
        session.add(row)
    if function == RaciFunction.CO:
        contract.owner_id = profile_id
    elif function == RaciFunction.CM:
        contract.manager_id = profile_id
    contract.updated_at = datetime.now(UTC)
    session.flush()
    audit.record(
        session,
        org_id=contract.organization_id,
        action=AuditAction.contract_role_assigned,
        actor=actor,
        object_kind="contract",
        object_id=contract.id,
        object_label=f"{contract.reference} {contract.name}",
        contract_id=contract.id,
        details={
            "function": function.value,
            "before": before,
            "after": str(profile_id) if profile_id else supplier_contact,
        },
    )
    return row


def sync_roles_from_contract(
    session: Session, contract: Contract, actor_id: uuid.UUID | None
) -> None:
    """ADR-0021 §2: owner_id/manager_id ARE the CO/CM rows. Called when a contract is
    created with owner/manager set, so the two read paths agree."""
    roles = active_roles(session, contract.id)
    today = date.today()
    for fn, pid in ((RaciFunction.CO, contract.owner_id), (RaciFunction.CM, contract.manager_id)):
        cur = roles.get(fn.value)
        if pid is None or (cur is not None and cur.profile_id == pid):
            continue
        if cur is not None:
            cur.until = today
            session.flush()
        session.add(
            ContractRole(
                organization_id=contract.organization_id,
                contract_id=contract.id,
                function=fn,
                profile_id=pid,
                since=today,
                assigned_by=actor_id,
            )
        )
    session.flush()


# ---- §4 templates ---------------------------------------------------------------------------


def templates_for(session: Session, contract: Contract) -> list[RaciTemplate]:
    rows = session.scalars(select(RaciTemplate)).all()
    tier = contract.tier.value if contract.tier else None
    form = contract.agreement_form.value if contract.agreement_form else None
    out = []
    for t in rows:
        if t.tiers and (tier is None or tier not in t.tiers):
            continue
        if t.agreement_forms and (form is None or form not in t.agreement_forms):
            continue
        out.append(t)
    return out


# ---- §5 workload ----------------------------------------------------------------------------


@dataclass
class Load:
    profile_id: uuid.UUID
    name: str
    cm_contracts: int = 0
    co_contracts: int = 0
    weighted: int = 0
    open_items: int = 0
    functions: dict[str, int] = field(default_factory=lambda: defaultdict(int))


def policy_for(session: Session, org_id: uuid.UUID) -> WorkloadPolicy:
    p = session.get(WorkloadPolicy, org_id)
    if p is None:
        p = WorkloadPolicy(
            organization_id=org_id,
            max_weighted=30,
            max_cm_contracts=15,
            tier_weights={"N1": 3, "N2": 2, "N3": 1, "N4": 1},
        )
        session.add(p)
        session.flush()
    return p


def workload(session: Session, org_id: uuid.UUID) -> dict[uuid.UUID, Load]:
    """Per active user: contracts as CM/CO (tier-weighted), open items as responsible."""
    policy = policy_for(session, org_id)
    weights = policy.tier_weights or {}
    contracts = {
        c.id: c
        for c in session.scalars(
            select(Contract).where(
                Contract.status.notin_(
                    (ContractStatus.arkiveret, ContractStatus.udloebet, ContractStatus.opsagt)
                )
            )
        ).all()
    }
    profiles = {
        p.id: p
        for p in session.scalars(select(Profile).where(Profile.deactivated_at.is_(None))).all()
    }
    loads: dict[uuid.UUID, Load] = {}

    def load(pid: uuid.UUID) -> Load | None:
        p = profiles.get(pid)
        if p is None:
            return None
        return loads.setdefault(pid, Load(profile_id=pid, name=p.name))

    for r in session.scalars(
        select(ContractRole).where(
            ContractRole.until.is_(None), ContractRole.profile_id.is_not(None)
        )
    ):
        c = contracts.get(r.contract_id)
        if c is None or r.profile_id is None:
            continue
        ld = load(r.profile_id)
        if ld is None:
            continue
        ld.functions[r.function.value] += 1
        w = int(weights.get(c.tier.value if c.tier else "", 1) or 1)
        if r.function == RaciFunction.CM:
            ld.cm_contracts += 1
            ld.weighted += w
        elif r.function == RaciFunction.CO:
            ld.co_contracts += 1
            ld.weighted += w
    for o in session.scalars(
        select(Obligation).where(
            Obligation.status == ObligationStatus.aaben, Obligation.responsible_id.is_not(None)
        )
    ):
        if o.responsible_id and (ld := load(o.responsible_id)):
            ld.open_items += 1
    for t in session.scalars(
        select(Task).where(Task.status != TaskStatus.lukket, Task.responsible_id.is_not(None))
    ):
        if t.responsible_id and (ld := load(t.responsible_id)):
            ld.open_items += 1
    return loads


def least_loaded(
    loads: dict[uuid.UUID, Load], session: Session, function: str, exclude: set[uuid.UUID]
) -> Load | None:
    """ADR-0021 afklaring 3: the candidate is the lowest weighted load among people who
    already hold the function somewhere (same 'department' is not modelled yet)."""
    holders = [
        ld for ld in loads.values() if ld.functions.get(function) and ld.profile_id not in exclude
    ]
    if not holders:
        return None
    return min(holders, key=lambda ld: (ld.weighted, ld.open_items, ld.name))


# ---- §3 gap rules ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    rule: str
    contract_id: uuid.UUID | None
    object_ref: str
    title: str
    description: str
    priority: str = "mellem"
    candidate_id: uuid.UUID | None = None
    candidate_name: str | None = None


def find_gaps(
    session: Session, org_id: uuid.UUID, contract_ids: list[uuid.UUID] | None = None
) -> list[Finding]:
    contracts = {
        c.id: c
        for c in session.scalars(
            select(Contract).where(Contract.status.notin_((ContractStatus.arkiveret,)))
        ).all()
    }
    if contract_ids is not None:
        contracts = {k: v for k, v in contracts.items() if k in set(contract_ids)}
    profiles = {p.id: p for p in session.scalars(select(Profile)).all()}
    loads = workload(session, org_id)
    findings: list[Finding] = []

    def deactivated(pid: uuid.UUID | None) -> Profile | None:
        p = profiles.get(pid) if pid else None
        return p if p is not None and p.deactivated_at is not None else None

    for c in contracts.values():
        label = f"{c.reference} {c.name}"
        roles = active_roles(session, c.id)
        acts = session.scalars(
            select(RaciActivity).where(
                RaciActivity.contract_id == c.id, RaciActivity.status == RaciStatus.godkendt
            )
        ).all()
        for a in acts:
            cells = cells_of(session, a.id)
            letters = list(cells.values())
            if "A" not in letters:
                findings.append(
                    Finding(
                        "G1",
                        c.id,
                        f"activity:{a.id}",
                        f"RA-{a.seq} {a.name} har ingen ansvarlig (A)",
                        f"Aktiviteten på {label} mangler et A. Vælg den funktion, der er ansvarlig.",
                        "hoej",
                    )
                )
            for fn, letter in cells.items():
                if letter in ("A", "R") and fn != "LEV" and fn not in roles:
                    findings.append(
                        Finding(
                            "G2",
                            c.id,
                            f"activity:{a.id}:{fn}",
                            f"{FUNCTION_NAMES[fn]} er {letter} på RA-{a.seq}, men ingen person har funktionen",
                            f"På {label} er {FUNCTION_NAMES[fn]} {letter} for '{a.name}', men contract_roles har ingen aktiv person for {fn}. Tilknyt en person.",
                            "hoej"
                            if a.criticality in (Criticality.hoej, Criticality.kritisk)
                            else "mellem",
                        )
                    )
            if a.criticality in (Criticality.hoej, Criticality.kritisk):
                a_fn = next((fn for fn, letter in cells.items() if letter == "A"), None)
                if (
                    a_fn
                    and cells.get(a_fn) == "A"
                    and [fn for fn, letter in cells.items() if letter == "R"] == []
                    and a_fn not in roles
                ):
                    pass  # covered by G2 (no R at all is a validation error)
                elif (
                    a_fn
                    and all(fn == a_fn for fn, letter in cells.items() if letter in ("A", "R"))
                    and a_fn not in roles
                ):
                    findings.append(
                        Finding(
                            "G7",
                            c.id,
                            f"activity:{a.id}:G7",
                            f"RA-{a.seq} {a.name}: A og R er samme tomme funktion",
                            f"På {label} bærer {FUNCTION_NAMES[a_fn]} både A og R for en højkritisk aktivitet, og funktionen er ubesat.",
                            "hoej",
                        )
                    )
        for fn, r in roles.items():
            p = deactivated(r.profile_id)
            if p is None:
                continue
            cand = least_loaded(loads, session, fn, exclude={p.id})
            findings.append(
                Finding(
                    "G3",
                    c.id,
                    f"role:{c.id}:{fn}",
                    f"{p.name} er fratrådt, men er stadig {FUNCTION_NAMES[fn]} på {c.reference}",
                    f"{p.name} er deaktiveret og står stadig som {FUNCTION_NAMES[fn]} på {label}."
                    + (
                        f" Foreslået: {cand.name} (vægtet belastning {cand.weighted}, {cand.cm_contracts} kontrakter som CM)."
                        if cand
                        else " Ingen kandidat med samme funktion — tildel manuelt."
                    ),
                    "hoej",
                    cand.profile_id if cand else None,
                    cand.name if cand else None,
                )
            )
        for o in session.scalars(
            select(Obligation).where(
                Obligation.contract_id == c.id, Obligation.status == ObligationStatus.aaben
            )
        ):
            p = deactivated(o.responsible_id)
            if p is not None:
                findings.append(
                    Finding(
                        "G4",
                        c.id,
                        f"obligation:{o.id}",
                        f"F-{o.seq} {o.title}: ansvarlig {p.name} er fratrådt",
                        f"Forpligtelsen på {label} har en deaktiveret ansvarlig. Vælg en ny.",
                        "mellem",
                    )
                )
        for rk in session.scalars(
            select(Risk).where(Risk.contract_id == c.id, Risk.status != RiskStatus.lukket)
        ):
            p = deactivated(rk.responsible_id)
            if p is not None:
                findings.append(
                    Finding(
                        "G4",
                        c.id,
                        f"risk:{rk.id}",
                        f"R-{rk.seq} {rk.title}: ansvarlig {p.name} er fratrådt",
                        f"Risikoen på {label} har en deaktiveret ansvarlig. Vælg en ny.",
                        "mellem",
                    )
                )
        for t in session.scalars(
            select(Task).where(Task.contract_id == c.id, Task.status != TaskStatus.lukket)
        ):
            p = deactivated(t.responsible_id)
            if p is not None:
                findings.append(
                    Finding(
                        "G4",
                        c.id,
                        f"task:{t.id}",
                        f"T-{t.seq} {t.title}: ansvarlig {p.name} er fratrådt",
                        f"Opgaven på {label} har en deaktiveret ansvarlig. Vælg en ny.",
                        "mellem",
                    )
                )
        if c.confidentiality == Confidentiality.fortrolig:
            holders = [pid for pid in (c.owner_id, c.manager_id) if pid and not deactivated(pid)]
            access = session.scalars(
                select(ContractAccess).where(ContractAccess.contract_id == c.id)
            ).all()
            active_access = [
                x
                for x in access
                if getattr(x, "revoked_at", None) is None
                and not deactivated(getattr(x, "profile_id", None))
            ]
            if not holders and not active_access:
                findings.append(
                    Finding(
                        "G5",
                        c.id,
                        f"contract:{c.id}:G5",
                        f"{c.reference} er fortrolig uden aktive adgangshavere",
                        f"Ingen aktiv ejer, manager eller adgangshaver kan se {label}. Tildel adgang.",
                        "hoej",
                    )
                )
        if c.phase == ContractPhase.aktiv_drift:
            for fn in ("CM", "CO"):
                if fn not in roles or deactivated(roles[fn].profile_id):
                    findings.append(
                        Finding(
                            "G6",
                            c.id,
                            f"contract:{c.id}:{fn}",
                            f"{c.reference} er i aktiv drift uden {FUNCTION_NAMES[fn]}",
                            f"{label} er i aktiv drift, men funktionen {fn} er ubesat. Tilknyt en person.",
                            "hoej",
                        )
                    )
    return findings


def write_findings(
    session: Session,
    *,
    org_id: uuid.UUID,
    agent_key: str,
    label: str,
    findings: list[Finding],
    agent_run_id: uuid.UUID | None,
    scope_contract_ids: list[uuid.UUID] | None = None,
) -> tuple[int, int, int]:
    """Upsert one task-proposal per finding (fingerprint = rule + object); expire the
    agent's open proposals that were not produced this run — a closed gap closes its
    task automatically (ADR-0021 §3). Returns (created, updated, closed)."""
    created = updated = 0
    seen: set[str] = set()
    for f in findings:
        if f.contract_id is None:
            continue
        fp = suggestions.fingerprint(
            agent_key, f.contract_id, SuggestionSubject.task, f.rule, f.object_ref
        )
        seen.add(fp)
        payload: dict[str, Any] = {
            "title": f.title,
            "description": f.description,
            "priority": f.priority,
            "rule": f.rule,
            "object_ref": f.object_ref,
            "candidate_id": str(f.candidate_id) if f.candidate_id else None,
            "candidate_name": f.candidate_name,
        }
        _, was_created = suggestions.upsert(
            session,
            org_id=org_id,
            contract_id=f.contract_id,
            agent_key=agent_key,
            agent_label=label,
            agent_run_id=agent_run_id,
            kind=SuggestionKind.create,
            subject_kind=SuggestionSubject.task,
            subject_id=None,
            payload=payload,
            confidence=Confidence.hoej,
            rationale=f"Regel {f.rule}: {f.description}",
            citations=[
                {
                    "kind": "record",
                    "record_kind": "rule",
                    "record_id": None,
                    "label": f"Regel {f.rule}",
                    "verified": True,
                }
            ],
            fp=fp,
        )
        created += int(was_created)
        updated += int(not was_created)
    q = select(AiSuggestion).where(
        AiSuggestion.agent_key == agent_key,
        AiSuggestion.subject_kind == SuggestionSubject.task,
        AiSuggestion.status.in_(OPEN_SUGGESTION_STATUSES),
    )
    if scope_contract_ids is not None:
        q = q.where(AiSuggestion.contract_id.in_(scope_contract_ids))
    closed = 0
    now = datetime.now(UTC)
    for s in session.scalars(q).all():
        if s.fingerprint in seen:
            continue
        s.status = SuggestionStatus.foraeldet
        s.decision_comment = "hullet er lukket — fundet gentog sig ikke ved seneste kørsel"
        s.updated_at = now
        closed += 1
    session.flush()
    return created, updated, closed


def workload_findings(session: Session, org_id: uuid.UUID) -> list[Finding]:
    """ADR-0021 §5: over the threshold → one finding with a named candidate."""
    policy = policy_for(session, org_id)
    loads = workload(session, org_id)
    out: list[Finding] = []
    for ld in loads.values():
        over = []
        if ld.weighted > policy.max_weighted:
            over.append(f"vægtet kontraktsum {ld.weighted} > {policy.max_weighted}")
        if ld.cm_contracts > policy.max_cm_contracts:
            over.append(f"{ld.cm_contracts} kontrakter som CM > {policy.max_cm_contracts}")
        if not over:
            continue
        cand = least_loaded(loads, session, "CM", exclude={ld.profile_id})
        # attach to the person's heaviest contract as CM so RLS scopes it sensibly
        role = session.scalars(
            select(ContractRole).where(
                ContractRole.profile_id == ld.profile_id,
                ContractRole.until.is_(None),
                ContractRole.function == RaciFunction.CM,
            )
        ).first()
        out.append(
            Finding(
                "W1",
                role.contract_id if role else None,
                f"profile:{ld.profile_id}",
                f"{ld.name} er over belastningsgrænsen ({'; '.join(over)})",
                f"{ld.name}: {ld.cm_contracts} kontrakter som CM, {ld.co_contracts} som CO, vægtet {ld.weighted}, {ld.open_items} åbne poster."
                + (
                    f" Foreslået flytning til {cand.name} (vægtet {cand.weighted})."
                    if cand
                    else " Ingen kandidat fundet."
                ),
                "mellem",
                cand.profile_id if cand else None,
                cand.name if cand else None,
            )
        )
    return out


# ---- materialisation: task ----------------------------------------------------------------------


def next_task_seq(session: Session, org_id: uuid.UUID) -> int:
    return (
        int(
            session.scalar(
                select(func.coalesce(func.max(Task.seq), 0)).where(Task.organization_id == org_id)
            )
            or 0
        )
        + 1
    )


def create_task(
    session: Session,
    *,
    org_id: uuid.UUID,
    contract_id: uuid.UUID | None,
    title: str,
    description: str | None,
    responsible_id: uuid.UUID | None,
    deadline: date | None,
    priority: str,
    origin: Any,
    origin_kind: str | None,
    origin_ref: str | None,
    actor: audit.Actor,
    actor_id: uuid.UUID | None,
    suggestion_id: uuid.UUID | None = None,
) -> Task:
    from app.domain.models import TaskPriority

    now = datetime.now(UTC)
    t = Task(
        organization_id=org_id,
        contract_id=contract_id,
        seq=next_task_seq(session, org_id),
        title=title.strip()[:200],
        description=description,
        responsible_id=responsible_id,
        deadline=deadline,
        priority=TaskPriority(priority)
        if priority in TaskPriority.__members__
        else TaskPriority.mellem,
        origin=origin,
        origin_kind=origin_kind,
        origin_ref=origin_ref,
        suggestion_id=suggestion_id,
        created_by=actor_id,
        created_at=now,
        updated_at=now,
    )
    session.add(t)
    session.flush()
    audit.record(
        session,
        org_id=org_id,
        action=AuditAction.task_created,
        actor=actor,
        object_kind="task",
        object_id=t.id,
        object_label=f"T-{t.seq} {t.title}",
        contract_id=contract_id,
        details={
            "origin": str(getattr(origin, "value", origin)),
            "origin_kind": origin_kind,
            "priority": priority,
        },
    )
    return t
