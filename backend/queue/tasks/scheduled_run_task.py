"""Task Celery — execucao de ScheduledReport via fila.

Substitui a execucao inline do `schedules_routes.run-now`.

Fluxo:
1. Carrega `ScheduledReport` pelo ID.
2. Cria um `Job(kind='scheduled_report')` para rastreamento.
3. Roda as 3 etapas isoladas (consulta Protheus, geracao xlsx/csv, envio SMTP)
   reusando as mesmas funcoes do `scheduler._execute_schedule`.
4. Atualiza `last_run_at`, `last_status`, `last_error` no ScheduledReport.

Ganho vs inline:
- Endpoint `run-now` retorna 202 + job_id imediatamente — UI nao bloqueia.
- Frontend pode pollar /api/reports/jobs/{job_id} para acompanhar.
- Falha grave do worker nao derruba o uvicorn.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from ... import jobs as jobs_mod
from ...database import SessionLocal
from ...models import AuditLog, ScheduledReport
from ..celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="schedules.run_scheduled", bind=True)
def run_scheduled(self, schedule_id: int, job_id: str | None = None) -> dict:
    """Roda um agendamento pela fila.

    Args:
        schedule_id: ID da linha em scheduled_reports.
        job_id:      Job opcional ja criado pelo router (para o front pollar).
    """
    # late-import para nao circular com scheduler/email
    from ...scheduler import _execute_schedule

    db = SessionLocal()
    try:
        s = db.query(ScheduledReport).filter(ScheduledReport.id == schedule_id).first()
        if not s:
            if job_id:
                jobs_mod.mark_failed(job_id, "ERR-JOB-006", f"ScheduledReport #{schedule_id} nao existe")
            return {"ok": False, "reason": "schedule_not_found"}

        if job_id:
            jobs_mod.mark_running(job_id, celery_task_id=self.request.id)
            jobs_mod.update_progress(job_id, 0, rows_total=None)

        # Reusa a logica existente (isolada em 3 try/except por etapa)
        status_, err = _execute_schedule(s)

        # Atualiza o proprio ScheduledReport
        s.last_run_at = datetime.utcnow()
        s.last_status = status_
        s.last_error = err
        db.add(AuditLog(
            username="(celery)",
            action="schedule.run_via_queue",
            detail=f"id={s.id} '{s.name}' -> {status_}" + (f" | {err}" if err else ""),
            success=(status_ == "success"),
        ))
        db.commit()

        if job_id:
            if status_ == "success":
                jobs_mod.mark_done(
                    job_id,
                    file_path="",  # envio por e-mail; nao expoe download
                    file_size=0,
                    rows=0,
                )
            else:
                jobs_mod.mark_failed(job_id, "ERR-JOB-005", err or "Falha desconhecida")

        return {"ok": status_ == "success", "status": status_, "error": err}
    finally:
        db.close()
