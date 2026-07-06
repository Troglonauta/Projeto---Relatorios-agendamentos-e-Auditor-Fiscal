"""Endpoints REST para a fila de relatorios (Fase 3).

Fluxo:
- POST /api/reports/jobs        enfileira (202 + job_id)
- GET  /api/reports/jobs/{id}   status + progresso
- GET  /api/reports/jobs/{id}/download   FileResponse (se status=done)
- DELETE /api/reports/jobs/{id} cancela
- GET  /api/reports/jobs        lista (mine=true por padrao p/ operador)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import audit, jobs, protheus_api
from ..config import get_settings
from ..database import get_db
from ..deps import (
    assert_table_allowed, get_client_ip, get_current_user, require_action,
)
from ..models import Job, User
from ..protheus_aliases import is_known_alias
from ..protheus_api import ProtheusError
from ..schemas import ProtheusQueryRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/reports/jobs", tags=["reports-jobs"])
_settings = get_settings()


def _job_dict(j: Job) -> dict:
    return {
        "id": j.id,
        "kind": j.kind,
        "status": j.status,
        "progress_pct": j.progress_pct,
        "rows_processed": j.rows_processed,
        "rows_total": j.rows_total,
        "created_at": j.created_at.isoformat() if j.created_at else None,
        "started_at": j.started_at.isoformat() if j.started_at else None,
        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
        "file_size_bytes": j.file_size_bytes,
        "error_code": j.error_code,
        "error_detail": j.error_detail,
        "celery_task_id": j.celery_task_id,
    }


@router.post("", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(require_action("export"))])
def enqueue_report(
    payload: ProtheusQueryRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Enfileira a geracao de um relatorio pesado. Retorna 202 + job_id."""
    # Resolve tabela fisica
    if payload.alias:
        if not is_known_alias(payload.alias):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Alias desconhecido")
        if not payload.branch:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Informe a filial")
        try:
            table = protheus_api.resolve_table_name(
                payload.alias, payload.branch, _settings.PROTHEUS_TABLE_SUFFIX,
            )
        except ProtheusError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    elif payload.table:
        table = payload.table.upper()
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Informe alias+branch ou table")

    alias_for_acl = (payload.alias or table[:3]).upper()
    assert_table_allowed(user, alias_for_acl)
    # Sprint 5 — em modo JOIN, valida whitelist de cada tabela do JOIN
    if payload.joins:
        for j in payload.joins:
            assert_table_allowed(user, j.alias.upper())

    # Normaliza filtros (rules visual -> dict)
    filters: Optional[dict] = None
    if payload.rules:
        from .protheus_routes import _normalize_filters
        filters = _normalize_filters(payload)
    elif payload.filters:
        filters = payload.filters

    file_format = "xlsx"  # default
    if "file_format" in request.query_params:
        file_format = request.query_params["file_format"].lower()

    job_payload = {
        "table": table,
        "alias": payload.alias.upper() if payload.alias else None,
        "branch": payload.branch,
        "columns": payload.columns,
        "filters": filters,
        "file_format": file_format,
    }
    # Sprint 5 — JOINs no payload do worker
    if payload.joins:
        job_payload["joins"] = [
            {
                "alias": j.alias.upper(), "branch": j.branch,
                "join_type": j.join_type.upper(),
                "on": [
                    {"left_alias": c.left_alias.upper(),
                     "left_column": c.left_column.upper(),
                     "right_column": c.right_column.upper()}
                    for c in j.on
                ],
            }
            for j in payload.joins
        ]

    job = jobs.create_job("report", job_payload, owner_id=user.id, db=db)

    # Enfileira no Celery (late-import para evitar carregar Celery quando o
    # backend estiver rodando sem o worker ativo)
    try:
        from ..queue.tasks.report_task import generate_report
        async_result = generate_report.apply_async(args=[job.id])
        job.celery_task_id = async_result.id
        db.commit()
    except Exception as exc:
        # Se nao conseguiu enfileirar, marca como failed com ERR-JOB-001.
        logger.exception("Falha ao enfileirar job %s", job.id)
        jobs.mark_failed(job.id, "ERR-JOB-001", f"Fila indisponivel: {exc}")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Fila indisponivel: {exc}",
        )

    audit.log(db, action="report.job.enqueue", user=user, ip=get_client_ip(request),
              detail=f"job={job.id} table={table}")
    return {"job_id": job.id, "status": "queued"}


@router.get("", dependencies=[Depends(require_action("view"))])
def list_jobs(
    mine: bool = Query(True),
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(Job)
    if mine or user.role != "admin":
        q = q.filter(Job.owner_id == user.id)
    if status_filter:
        q = q.filter(Job.status == status_filter)
    items = q.order_by(Job.created_at.desc()).limit(limit).all()
    return [_job_dict(j) for j in items]


@router.get("/{job_id}", dependencies=[Depends(require_action("view"))])
def get_job_status(job_id: str, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    j = db.query(Job).filter(Job.id == job_id).first()
    if not j:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job nao encontrado")
    if user.role != "admin" and j.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sem acesso a este job")
    return _job_dict(j)


@router.get("/{job_id}/download", dependencies=[Depends(require_action("export"))])
def download_job(job_id: str, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    j = db.query(Job).filter(Job.id == job_id).first()
    if not j:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job nao encontrado")
    if user.role != "admin" and j.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sem acesso a este job")
    if j.status != "done":
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"Job em status '{j.status}' — aguarde concluir")
    if not j.file_path or not Path(j.file_path).exists():
        raise HTTPException(status.HTTP_410_GONE, "Arquivo nao esta mais disponivel")
    name = Path(j.file_path).name
    return FileResponse(j.file_path, filename=name)


@router.delete("/{job_id}", dependencies=[Depends(require_action("schedule"))])
def cancel_job(job_id: str, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    j = db.query(Job).filter(Job.id == job_id).first()
    if not j:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job nao encontrado")
    if user.role != "admin" and j.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sem acesso a este job")
    ok = jobs.request_cancel(job_id)
    if not ok:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Job em status '{j.status}' nao e cancelavel")
    return {"detail": "Cancelamento solicitado"}
