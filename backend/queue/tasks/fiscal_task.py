"""Task Celery do Auditor Fiscal — Sprint 12 (Auditoria Interna).

Roteiro:
1. Carrega Job pelo ID, marca running.
2. Decodifica payload {date_from, date_to, branches, chave_filter, doc_models}.
3. Chama `backend.fiscal.auditor.run_audit(...)` com progress/cancel callbacks.
4. Marca done com stats; on-error marca failed com codigo apropriado.

Sprint 12: removidos hora_from/hora_to (filtro F1_HORA do antigo motor SF1)
e a logica de excesso de erros (sem rede, sem `AuditAborted`).
"""
from __future__ import annotations

import json
import logging
from datetime import date as _date, datetime as _dt
from pathlib import Path

from ... import jobs as jobs_mod
from ...config import get_settings
from ...fiscal.auditor import run_audit
from ..celery_app import celery_app

logger = logging.getLogger(__name__)
settings = get_settings()
OUTPUT_DIR = Path(settings.REPORTS_OUTPUT_DIR)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _coerce_date(v) -> _date:
    if isinstance(v, _date) and not isinstance(v, _dt):
        return v
    if isinstance(v, _dt):
        return v.date()
    if isinstance(v, str):
        return _date.fromisoformat(v)
    raise ValueError(f"date inválido (tipo {type(v).__name__}): {v!r}")


@celery_app.task(name="fiscal.run_audit", bind=True)
def run_fiscal_audit(self, job_id: str) -> dict:
    job = jobs_mod.get_job(job_id)
    if not job:
        return {"ok": False, "reason": "job_not_found"}

    jobs_mod.mark_running(job_id, celery_task_id=self.request.id)

    try:
        payload = json.loads(job.payload_json)

        date_from = _coerce_date(payload["date_from"])
        date_to = _coerce_date(payload["date_to"])
        branches = payload.get("branches") or []
        chave_filter = payload.get("chave_filter")
        doc_models = payload.get("doc_models")
        if doc_models:
            doc_models = set(doc_models)
        autonomous_mode = bool(payload.get("autonomous_mode"))
        notify_email = bool(payload.get("notify_email"))   # v2.30 — e-mail on-demand
        # Sprint 20 — motor selecionado (default: internal)
        engine_kind = (payload.get("engine") or "internal").strip()

        def _progress(processed, total):
            jobs_mod.update_progress(job_id, processed, rows_total=total)

        def _cancel():
            return jobs_mod.is_cancel_requested(job_id)

        stats = run_audit(
            date_from=date_from, date_to=date_to, branches=branches,
            chave_filter=chave_filter, doc_models=doc_models,
            job_id=job_id, progress_cb=_progress, cancel_cb=_cancel,
            autonomous_mode=autonomous_mode, notify_email=notify_email,
            engine_kind=engine_kind,
        )

        # Persiste stats como arquivo JSON anexo do job.
        out_path = OUTPUT_DIR / f"fiscal_audit_{job_id[:8]}.json"
        out_path.write_text(
            json.dumps(stats, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        if stats.get("canceled"):
            jobs_mod.mark_canceled(job_id)
            return {"ok": False, "reason": "canceled"}

        # Webhook de alerta (Teams/Slack/generico) — best-effort.
        try:
            from ...fiscal.webhook import send_critical_alert
            result = send_critical_alert(stats)
            if result and result.get("ok"):
                stats["webhook_sent"] = True
            elif result:
                stats["webhook_error"] = result.get("detail", "falha")
        except Exception as exc:
            logger.warning("Webhook fiscal falhou (segue mesmo assim): %s", exc)

        jobs_mod.mark_done(
            job_id,
            file_path=str(out_path),
            file_size=out_path.stat().st_size,
            rows=stats.get("docs_audited", 0),
        )
        return {"ok": True, **stats}

    except Exception as exc:
        logger.exception("Falha no fiscal job %s", job_id)
        # ERR-FISCAL-005 = falha generica no motor interno
        jobs_mod.mark_failed(job_id, "ERR-FISCAL-005", str(exc))
        return {"ok": False, "reason": "error", "detail": str(exc)}
