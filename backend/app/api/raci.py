"""Ansvar og governance over HTTP (ADR-0021) + tasks + org-level rule agents.

GET   /api/members                                     people in the org (pickers)
GET   /api/contracts/{id}/raci                         activities × cells, roles, gaps
POST  /api/contracts/{id}/raci/activities              manual activity        [raci_godkend]
PUT   /api/raci/activities/{id}/cells/{function}       set/clear a cell       [raci_godkend]
PATCH /api/raci/activities/{id}                        name/criticality       [raci_godkend]
PUT   /api/contracts/{id}/roles/{function}             staff a function       [raci_godkend]
GET   /api/contracts/{id}/tasks · GET /api/tasks       open tasks
POST  /api/contracts/{id}/tasks · PATCH /api/tasks/{id}                       [kontrakt_red]
(org-wide "kør nu" lives in app/api/agents.py — ADR-0010)
GET   /api/workload                                    §5 numbers            [brugere]
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    CitationOut,
    ContractRoleOut,
    MemberOut,
    RaciActivityCreate,
    RaciActivityOut,
    RaciActivityPatch,
    RaciCellIn,
    RaciOut,
    RoleAssignIn,
    TaskCreate,
    TaskOut,
    TaskPatch,
    WorkloadOut,
)
from app.core import access, audit
from app.core.auth import Principal, current_principal, require, tenant_session
from app.core.db import SessionLocal
from app.domain.models import (
    AuditAction,
    Citation,
    Contract,
    OrganizationMember,
    Origin,
    Profile,
    RaciActivity,
    RaciFunction,
    Task,
    TaskStatus,
)
from app.raci import service

router = APIRouter(prefix="/api", tags=["raci"])


def _contract_or_404(session: Session, contract_id: uuid.UUID) -> Contract:
    c = session.get(Contract, contract_id)
    if c is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"error": "Kontrakten findes ikke", "code": "not_found"},
        )
    return c


def _raise(e: service.RaciError) -> HTTPException:
    return HTTPException(e.status, detail={"error": str(e), "code": e.code})


# ---- members --------------------------------------------------------------------------------


@router.get("/members", response_model=list[MemberOut])
def members(principal: Principal = Depends(current_principal)) -> list[MemberOut]:
    # Identity tables carry no RLS; scope by the caller's org explicitly.
    with SessionLocal() as s:
        rows = s.execute(
            select(Profile, OrganizationMember.role)
            .join(OrganizationMember, OrganizationMember.profile_id == Profile.id)
            .where(OrganizationMember.organization_id == principal.org_id)
            .order_by(Profile.name)
        ).all()
        return [
            MemberOut(
                id=p.id,
                name=p.name,
                email=p.email,
                role=role.value,
                deactivated=p.deactivated_at is not None,
            )
            for p, role in rows
        ]


# ---- the matrix -----------------------------------------------------------------------------


def _activity_out(session: Session, a: RaciActivity) -> RaciActivityOut:
    cells = service.cells_of(session, a.id)
    cites = session.scalars(
        select(Citation).where(
            Citation.subject_kind == "raci_activity", Citation.subject_id == a.id
        )
    ).all()
    return RaciActivityOut(
        id=a.id,
        contract_id=a.contract_id,
        seq=a.seq,
        ref=f"RA-{a.seq}",
        name=a.name,
        criticality=a.criticality,
        status=a.status,
        template_key=a.template_key,
        origin=a.origin,
        cells=cells,
        validation_errors=service.validate(cells),
        citations=[CitationOut.model_validate(c) for c in cites],
    )


def _roles_out(session: Session, contract_id: uuid.UUID) -> list[ContractRoleOut]:
    roles = service.active_roles(session, contract_id)
    out = []
    with SessionLocal() as ident:
        for fn in RaciFunction:
            r = roles.get(fn.value)
            p = ident.get(Profile, r.profile_id) if r and r.profile_id else None
            out.append(
                ContractRoleOut(
                    function=fn,
                    label=service.FUNCTION_NAMES[fn.value],
                    profile_id=r.profile_id if r else None,
                    person_name=p.name if p else None,
                    deactivated=bool(p and p.deactivated_at),
                    supplier_contact=r.supplier_contact if r else None,
                    since=r.since if r else None,
                )
            )
    return out


@router.get("/contracts/{contract_id}/raci", response_model=RaciOut)
def get_raci(contract_id: uuid.UUID, session: Session = Depends(tenant_session)) -> RaciOut:
    _contract_or_404(session, contract_id)
    acts = session.scalars(
        select(RaciActivity)
        .where(RaciActivity.contract_id == contract_id)
        .order_by(RaciActivity.seq)
    ).all()
    return RaciOut(
        activities=[_activity_out(session, a) for a in acts],
        roles=_roles_out(session, contract_id),
        functions=[
            {"key": f.value, "label": service.FUNCTION_NAMES[f.value]} for f in RaciFunction
        ],
    )


@router.post(
    "/contracts/{contract_id}/raci/activities",
    response_model=RaciActivityOut,
    status_code=status.HTTP_201_CREATED,
)
def create_activity(
    contract_id: uuid.UUID,
    body: RaciActivityCreate,
    principal: Principal = Depends(require(access.RACI_GODKEND)),
    session: Session = Depends(tenant_session),
) -> RaciActivityOut:
    c = _contract_or_404(session, contract_id)
    try:
        a = service.create_activity(
            session,
            contract=c,
            name=body.name,
            criticality=body.criticality,
            cells=body.cells,
            actor=audit.human(principal),
            actor_id=principal.user_id,
            origin=Origin.human,
        )
    except service.RaciError as e:
        raise _raise(e) from e
    return _activity_out(session, a)


@router.put("/raci/activities/{activity_id}/cells/{function}", response_model=RaciActivityOut)
def set_cell(
    activity_id: uuid.UUID,
    function: RaciFunction,
    body: RaciCellIn,
    principal: Principal = Depends(require(access.RACI_GODKEND)),
    session: Session = Depends(tenant_session),
) -> RaciActivityOut:
    a = session.get(RaciActivity, activity_id)
    if a is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"error": "Aktiviteten findes ikke", "code": "not_found"},
        )
    try:
        service.set_cell(
            session,
            activity=a,
            function=function.value,
            letter=body.letter.value if body.letter else None,
            actor=audit.human(principal),
        )
    except service.RaciError as e:
        raise _raise(e) from e
    return _activity_out(session, a)


@router.patch("/raci/activities/{activity_id}", response_model=RaciActivityOut)
def patch_activity(
    activity_id: uuid.UUID,
    body: RaciActivityPatch,
    principal: Principal = Depends(require(access.RACI_GODKEND)),
    session: Session = Depends(tenant_session),
) -> RaciActivityOut:
    a = session.get(RaciActivity, activity_id)
    if a is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"error": "Aktiviteten findes ikke", "code": "not_found"},
        )
    changes = body.model_dump(exclude_unset=True)
    before = {k: str(getattr(a, k)) for k in changes}
    for k, v in changes.items():
        setattr(a, k, v)
    a.updated_at = datetime.now(UTC)
    session.flush()
    audit.record(
        session,
        org_id=a.organization_id,
        action=AuditAction.raci_activity_updated,
        actor=audit.human(principal),
        object_kind="raci_activity",
        object_id=a.id,
        object_label=f"RA-{a.seq} {a.name}",
        contract_id=a.contract_id,
        details={"before": before, "after": {k: str(v) for k, v in changes.items()}},
    )
    return _activity_out(session, a)


@router.put("/contracts/{contract_id}/roles/{function}", response_model=list[ContractRoleOut])
def assign_role(
    contract_id: uuid.UUID,
    function: RaciFunction,
    body: RoleAssignIn,
    principal: Principal = Depends(require(access.RACI_GODKEND)),
    session: Session = Depends(tenant_session),
) -> list[ContractRoleOut]:
    c = _contract_or_404(session, contract_id)
    if body.profile_id is not None:
        with SessionLocal() as ident:
            ok = ident.scalar(
                select(OrganizationMember.profile_id).where(
                    OrganizationMember.organization_id == principal.org_id,
                    OrganizationMember.profile_id == body.profile_id,
                )
            )
        if ok is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail={"error": "Personen er ikke medlem af organisationen", "code": "not_found"},
            )
    try:
        service.assign_role(
            session,
            contract=c,
            function=function,
            profile_id=body.profile_id,
            supplier_contact=body.supplier_contact,
            actor=audit.human(principal),
            actor_id=principal.user_id,
        )
    except service.RaciError as e:
        raise _raise(e) from e
    return _roles_out(session, contract_id)


# ---- tasks ------------------------------------------------------------------------------------


def _task_out(t: Task) -> TaskOut:
    return TaskOut(
        id=t.id,
        contract_id=t.contract_id,
        seq=t.seq,
        ref=f"T-{t.seq}",
        title=t.title,
        description=t.description,
        responsible_id=t.responsible_id,
        deadline=t.deadline,
        priority=t.priority,
        status=t.status,
        origin=t.origin,
        origin_kind=t.origin_kind,
        origin_ref=t.origin_ref,
        created_at=t.created_at,
        closed_at=t.closed_at,
    )


@router.get("/contracts/{contract_id}/tasks", response_model=list[TaskOut])
def contract_tasks(
    contract_id: uuid.UUID, session: Session = Depends(tenant_session)
) -> list[TaskOut]:
    _contract_or_404(session, contract_id)
    rows = session.scalars(
        select(Task).where(Task.contract_id == contract_id).order_by(Task.status, Task.seq.desc())
    ).all()
    return [_task_out(t) for t in rows]


@router.get("/tasks", response_model=list[TaskOut])
def my_tasks(
    principal: Principal = Depends(current_principal),
    session: Session = Depends(tenant_session),
) -> list[TaskOut]:
    rows = session.scalars(
        select(Task)
        .where(Task.status != TaskStatus.lukket)
        .order_by(Task.deadline.nulls_last(), Task.seq.desc())
        .limit(200)
    ).all()
    return [_task_out(t) for t in rows]


@router.post(
    "/contracts/{contract_id}/tasks", response_model=TaskOut, status_code=status.HTTP_201_CREATED
)
def create_task(
    contract_id: uuid.UUID,
    body: TaskCreate,
    principal: Principal = Depends(require(access.KONTRAKT_RED)),
    session: Session = Depends(tenant_session),
) -> TaskOut:
    c = _contract_or_404(session, contract_id)
    t = service.create_task(
        session,
        org_id=principal.org_id,
        contract_id=c.id,
        title=body.title,
        description=body.description,
        responsible_id=body.responsible_id,
        deadline=body.deadline,
        priority=body.priority.value,
        origin=Origin.human,
        origin_kind="manual",
        origin_ref=None,
        actor=audit.human(principal),
        actor_id=principal.user_id,
    )
    return _task_out(t)


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def patch_task(
    task_id: uuid.UUID,
    body: TaskPatch,
    principal: Principal = Depends(require(access.KONTRAKT_RED)),
    session: Session = Depends(tenant_session),
) -> TaskOut:
    t = session.get(Task, task_id)
    if t is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"error": "Opgaven findes ikke", "code": "not_found"}
        )
    changes = body.model_dump(exclude_unset=True)
    before = {k: str(getattr(t, k)) for k in changes}
    for k, v in changes.items():
        setattr(t, k, v)
    now = datetime.now(UTC)
    t.updated_at = now
    if "status" in changes:
        t.closed_at = now if t.status == TaskStatus.lukket else None
    session.flush()
    audit.record(
        session,
        org_id=t.organization_id,
        action=AuditAction.task_updated,
        actor=audit.human(principal),
        object_kind="task",
        object_id=t.id,
        object_label=f"T-{t.seq} {t.title}",
        contract_id=t.contract_id,
        details={"before": before, "after": {k: str(v) for k, v in changes.items()}},
    )
    return _task_out(t)


# ---- org-level agents and workload ------------------------------------------------------------


@router.get("/workload", response_model=list[WorkloadOut])
def workload(
    principal: Principal = Depends(require(access.BRUGERE)),
    session: Session = Depends(tenant_session),
) -> list[WorkloadOut]:
    loads = service.workload(session, principal.org_id)
    policy = service.policy_for(session, principal.org_id)
    return sorted(
        (
            WorkloadOut(
                profile_id=ld.profile_id,
                name=ld.name,
                cm_contracts=ld.cm_contracts,
                co_contracts=ld.co_contracts,
                weighted=ld.weighted,
                open_items=ld.open_items,
                functions=dict(ld.functions),
                over_threshold=ld.weighted > policy.max_weighted
                or ld.cm_contracts > policy.max_cm_contracts,
            )
            for ld in loads.values()
        ),
        key=lambda w: -w.weighted,
    )
