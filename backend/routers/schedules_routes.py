"""Agendamentos de envio automático de relatórios."""
import json
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .. import audit, protheus_api
from ..config import get_settings
from ..database import get_db
from ..deps import (
    assert_table_allowed, get_client_ip, get_current_user, require_action,
)
from ..models import ScheduledReport, User
from ..protheus_aliases import is_known_alias
from ..protheus_api import ProtheusError
from ..schemas import ScheduleCreate, ScheduleOut

router = APIRouter(prefix="/api/schedules", tags=["schedules"])
_settings = get_settings()


# Mesmas regras do router de protheus para manter coerência.
_OP_MAP = {
    "eq": "eq", "=": "eq", "igual": "eq",
    "ne": "ne", "<>": "ne", "diferente": "ne",
    "gt": "gt", ">": "gt", "maior": "gt",
    "gte": "gte", ">=": "gte", "maior_igual": "gte",
    "lt": "lt", "<": "lt", "menor": "lt",
    "lte": "lte", "<=": "lte", "menor_igual": "lte",
    "like": "like", "contem": "like", "contains": "like",
    "in": "in", "between": "between",
}


def _materialize(payload: ScheduleCreate) -> tuple[str, dict | None]:
    """Devolve (table_name_físico, filters_dict) já no formato interno.

    Aceita tanto (alias+branch+rules) quanto (table_name+filters).
    """
    if payload.alias:
        if not is_known_alias(payload.alias):
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"Alias '{payload.alias}' não está no catálogo")
        if not payload.branch:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "Informe a filial ao usar alias")
        try:
            table = protheus_api.resolve_table_name(
                payload.alias, payload.branch, _settings.PROTHEUS_TABLE_SUFFIX,
            )
        except ProtheusError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    elif payload.table_name:
        table = payload.table_name.upper()
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Informe `alias`+`branch` ou `table_name`")

    if payload.rules:
        flt: dict = {}
        for i, r in enumerate(payload.rules):
            op = _OP_MAP.get((r.op or "eq").lower())
            if not op:
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    f"Operador desconhecido: {r.op!r}")
            key = r.field if op == "eq" else f"{r.field}__{op}"
            if key in flt:
                key = f"{key}__r{i}"
            flt[key] = r.value
        return table, flt

    return table, payload.filters


def _to_out(s: ScheduledReport) -> ScheduleOut:
    return ScheduleOut.model_validate(s)


@router.get("", response_model=List[ScheduleOut],
            dependencies=[Depends(require_action("schedule"))])
def list_schedules(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    q = db.query(ScheduledReport)
    # Operador só enxerga os próprios; admin vê todos.
    if user.role != "admin":
        q = q.filter(ScheduledReport.owner_id == user.id)
    return [_to_out(s) for s in q.order_by(ScheduledReport.created_at.desc()).all()]


@router.post("", response_model=ScheduleOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_action("schedule"))])
def create_schedule(
    payload: ScheduleCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not payload.cron and not payload.run_at:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Informe um `cron` recorrente ou `run_at` único")

    table, filters = _materialize(payload)
    # Whitelist é por ALIAS — usa o explicitado ou os 3 primeiros chars.
    alias_for_acl = (payload.alias or table[:3]).upper()
    assert_table_allowed(user, alias_for_acl)

    s = ScheduledReport(
        name=payload.name,
        owner_id=user.id,
        table_name=table,
        columns=",".join(payload.columns) if payload.columns else None,
        filters=json.dumps(filters) if filters else None,
        file_format=payload.file_format,
        recipients=",".join(str(e) for e in payload.recipients),
        cron=payload.cron,
        run_at=payload.run_at,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    audit.log(db, action="schedule.create", user=user, ip=get_client_ip(request),
              detail=f"id={s.id} table={s.table_name}")
    return _to_out(s)


@router.post("/{schedule_id}/toggle", response_model=ScheduleOut,
             dependencies=[Depends(require_action("schedule"))])
def toggle_schedule(
    schedule_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = db.query(ScheduledReport).filter(ScheduledReport.id == schedule_id).first()
    if not s:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agendamento não encontrado")
    if user.role != "admin" and s.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sem acesso a este agendamento")
    s.is_active = not s.is_active
    db.commit()
    db.refresh(s)
    audit.log(db, action="schedule.toggle", user=user, ip=get_client_ip(request),
              detail=f"id={s.id} active={s.is_active}")
    return _to_out(s)


@router.delete("/{schedule_id}", dependencies=[Depends(require_action("schedule"))])
def delete_schedule(
    schedule_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = db.query(ScheduledReport).filter(ScheduledReport.id == schedule_id).first()
    if not s:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agendamento não encontrado")
    if user.role != "admin" and s.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sem acesso a este agendamento")
    db.delete(s)
    db.commit()
    audit.log(db, action="schedule.delete", user=user, ip=get_client_ip(request),
              detail=f"id={schedule_id}")
    return {"detail": "Agendamento removido"}


@router.post("/{schedule_id}/run-now", dependencies=[Depends(require_action("schedule"))])
def run_now(
    schedule_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Enfileira o agendamento na fila Celery — retorna 202 imediatamente.

    Vantagens vs execucao inline:
    - UI nao bloqueia: o admin recebe `job_id` para pollar /api/reports/jobs/{id}.
    - Falhas no SMTP/Excel nao afetam o uvicorn.
    - Em alta concorrencia, varios agendamentos podem rodar em paralelo (worker).

    Compatibilidade: o response body manteve o campo `status` para nao quebrar
    o frontend antigo. O front Fase 3 le `job_id` e usa o jobs.js polling.
    """
    from .. import jobs as jobs_mod

    s = db.query(ScheduledReport).filter(ScheduledReport.id == schedule_id).first()
    if not s:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agendamento não encontrado")
    if user.role != "admin" and s.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sem acesso a este agendamento")

    # Cria Job para rastreamento + audit, mesmo se a fila estiver fora
    job = jobs_mod.create_job(
        "scheduled_report",
        {"schedule_id": s.id, "name": s.name, "table": s.table_name},
        owner_id=user.id, db=db,
    )

    try:
        from ..queue.tasks.scheduled_run_task import run_scheduled
        async_result = run_scheduled.apply_async(args=[s.id, job.id])
        job.celery_task_id = async_result.id
        db.commit()
    except Exception as exc:
        # Sem worker / sem Redis — marca o job como failed mas nao 502 o endpoint
        jobs_mod.mark_failed(job.id, "ERR-JOB-001", f"Fila indisponivel: {exc}")
        audit.log(db, action="schedule.run_manual.queue_down", user=user,
                  ip=get_client_ip(request),
                  detail=f"id={s.id} job={job.id} err={exc}", success=False)
        return {
            "status": "error",
            "job_id": job.id,
            "detail": f"Fila indisponivel: {exc}",
            "error": "ERR-JOB-001",
        }

    audit.log(db, action="schedule.run_manual", user=user, ip=get_client_ip(request),
              detail=f"id={s.id} job={job.id} enfileirado", success=True)
    return {
        "status": "queued",
        "job_id": job.id,
        "detail": "Agendamento enfileirado. Acompanhe em /api/reports/jobs/{job_id}.",
    }
