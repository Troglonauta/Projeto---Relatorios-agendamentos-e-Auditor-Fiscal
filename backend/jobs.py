"""CRUD de Job + helpers para o worker reportar progresso.

Toda interacao com a tabela `jobs` passa por aqui — facilita o worker
(em processo separado) usar a mesma logica que o web.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import Job
from .timeutils import now_brt  # Ponto 5 (fuso): timestamps de job em BRT

logger = logging.getLogger(__name__)


# ---- Criacao -----------------------------------------------------------------

def create_job(
    kind: str,
    payload: dict,
    owner_id: Optional[int] = None,
    db: Optional[Session] = None,
) -> Job:
    own = False
    if db is None:
        db = SessionLocal(); own = True
    try:
        j = Job(
            id=str(uuid.uuid4()),
            kind=kind,
            owner_id=owner_id,
            payload_json=json.dumps(payload, ensure_ascii=False),
            status="queued",
        )
        db.add(j)
        db.commit()
        db.refresh(j)
        return j
    finally:
        if own:
            db.close()


# ---- Updates atomicos --------------------------------------------------------

def mark_running(job_id: str, celery_task_id: Optional[str] = None) -> None:
    _patch(job_id, status="running", started_at=now_brt(),
           celery_task_id=celery_task_id)


def mark_done(job_id: str, *, file_path: str, file_size: int, rows: int) -> None:
    _patch(
        job_id,
        status="done", finished_at=now_brt(),
        file_path=file_path, file_size_bytes=file_size,
        rows_total=rows, rows_processed=rows, progress_pct=100.0,
    )


def mark_failed(job_id: str, error_code: str, error_detail: str) -> None:
    _patch(
        job_id, status="failed", finished_at=now_brt(),
        error_code=error_code, error_detail=error_detail[:4000],
    )


def mark_canceled(job_id: str) -> None:
    _patch(job_id, status="canceled", finished_at=now_brt())


def update_progress(job_id: str, rows_processed: int, rows_total: Optional[int] = None) -> None:
    fields: dict[str, Any] = {"rows_processed": rows_processed}
    if rows_total:
        fields["rows_total"] = rows_total
        fields["progress_pct"] = min(100.0, (rows_processed / rows_total) * 100)
    _patch(job_id, **fields)


def request_cancel(job_id: str) -> bool:
    db = SessionLocal()
    try:
        j = db.query(Job).filter(Job.id == job_id).first()
        if not j or j.status not in ("queued", "running"):
            return False
        j.should_cancel = True
        db.commit()
        return True
    finally:
        db.close()


def is_cancel_requested(job_id: str) -> bool:
    db = SessionLocal()
    try:
        j = db.query(Job).filter(Job.id == job_id).first()
        return bool(j and j.should_cancel)
    finally:
        db.close()


def _patch(job_id: str, **fields) -> None:
    db = SessionLocal()
    try:
        db.query(Job).filter(Job.id == job_id).update(fields)
        db.commit()
    finally:
        db.close()


# ---- Recuperacao de orfaos ---------------------------------------------------

def reset_orphan_jobs() -> int:
    """Marca como failed todos os jobs em status `running` na subida do worker.

    Usado pelo `worker_ready` signal do Celery — protege contra kill -9 que deixou
    job preso.
    """
    db = SessionLocal()
    try:
        orphans = db.query(Job).filter(Job.status == "running").all()
        for j in orphans:
            j.status = "failed"
            j.finished_at = now_brt()
            j.error_code = "ERR-JOB-002"
            j.error_detail = "Worker reiniciado durante execucao"
        db.commit()
        return len(orphans)
    finally:
        db.close()


def get_job(job_id: str, db: Optional[Session] = None) -> Optional[Job]:
    own = False
    if db is None:
        db = SessionLocal(); own = True
    try:
        return db.query(Job).filter(Job.id == job_id).first()
    finally:
        if own:
            db.close()
