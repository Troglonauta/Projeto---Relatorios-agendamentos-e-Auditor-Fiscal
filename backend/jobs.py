"""CRUD de Job + helpers para o worker reportar progresso.

Toda interacao com a tabela `jobs` passa por aqui — facilita o worker
(em processo separado) usar a mesma logica que o web.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.exc import OperationalError
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
        # Fecha qualquer transacao de LEITURA aberta na sessao da requisicao (as
        # checagens de auth leem User/permissoes/sessao). Sem isso, o INSERT
        # abaixo herda o snapshot antigo do WAL e, se o worker gravou nesse meio,
        # o SQLite devolve "database is locked" (BUSY_SNAPSHOT) — que o
        # busy_timeout NAO espera. Comecando limpo, o INSERT so aguarda o lock.
        try:
            db.rollback()
        except Exception:
            pass

        last_err: Optional[Exception] = None
        for attempt in range(6):
            j = Job(
                id=str(uuid.uuid4()),
                kind=kind,
                owner_id=owner_id,
                payload_json=json.dumps(payload, ensure_ascii=False),
                status="queued",
            )
            db.add(j)
            try:
                db.commit()
                db.refresh(j)
                return j
            except OperationalError as exc:
                last_err = exc
                db.rollback()
                if "database is locked" not in str(exc).lower() or attempt == 5:
                    raise
                time.sleep(0.4 * (attempt + 1))
        raise last_err  # pragma: no cover
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


def set_celery_task_id(job_id: str, celery_task_id: str) -> None:
    """Grava o id da task do Celery (best-effort, sessao propria)."""
    _patch(job_id, celery_task_id=celery_task_id)


def _patch(job_id: str, **fields) -> None:
    """Atualiza campos do Job com retry em caso de "database is locked".

    NUNCA propaga o lock: um UPDATE (ex.: progresso) travar por contencao
    momentanea do SQLite nao pode derrubar a auditoria/relatorio inteiro. Se
    esgotar as tentativas, apenas registra e segue (orfaos sao reconciliados no
    proximo restart do worker)."""
    for attempt in range(8):
        db = SessionLocal()
        try:
            db.query(Job).filter(Job.id == job_id).update(fields)
            db.commit()
            return
        except OperationalError as exc:
            try: db.rollback()
            except Exception: pass
            if "database is locked" not in str(exc).lower() or attempt == 7:
                logger.warning("Job %s: nao gravou %s (%s) — seguindo",
                               job_id, list(fields.keys()), exc.__class__.__name__)
                return
            time.sleep(0.3 * (attempt + 1))
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
