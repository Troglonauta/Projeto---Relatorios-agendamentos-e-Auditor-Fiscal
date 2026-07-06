"""Worker em background com APScheduler.

Roda a cada `SCHEDULER_INTERVAL_MINUTES` minutos (padrão 60). A cada tick:
- Lê todos os agendamentos ativos (`scheduled_reports.is_active=True`).
- Para cada um, decide se deve disparar AGORA com base em:
    - `cron`: se a expressão match a janela atual.
    - `run_at`: se for único e a hora atual >= run_at e ainda não foi rodado.
- Gera o relatório, envia por e-mail e atualiza last_run_at/last_status.

Resiliência:
- Cada etapa (consulta Protheus, geração de arquivo, envio SMTP) é
  isolada em try/except específico. Falhas viram `last_status='error'`
  com mensagem detalhada — NUNCA propagam exceção do FastAPI (502).
- Toda falha é registrada no AuditLog para o painel de auditoria.
"""
from __future__ import annotations

import json
import logging
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from . import protheus_api
from .config import get_settings
from .database import SessionLocal
from .email_service import send_email, send_report
from .models import AuditLog, ScheduledReport
from .protheus_api import ProtheusError
from .timeutils import now_brt as _now_brt  # Ponto 5 (fuso): last_run_at em BRT
from .reports import generate

logger = logging.getLogger(__name__)
settings = get_settings()

TZ = ZoneInfo("America/Sao_Paulo")

scheduler = BackgroundScheduler(timezone=TZ)


def now_br() -> datetime:
    """Datetime atual no fuso oficial da aplicação (America/Sao_Paulo)."""
    return datetime.now(TZ)


def _cron_matches_now(cron_expr: str, now: datetime, window_minutes: int) -> bool:
    """True se a próxima execução do cron caiu dentro da janela [now - window, now]."""
    try:
        trigger = CronTrigger.from_crontab(cron_expr, timezone=TZ)
    except Exception as exc:
        logger.error("Cron inválido %r: %s", cron_expr, exc)
        return False
    candidate = trigger.get_next_fire_time(None, now - timedelta(minutes=window_minutes))
    if candidate is None:
        return False
    return candidate <= now


def _execute_schedule(s: ScheduledReport) -> tuple[str, str | None]:
    """Roda um agendamento. Retorna (status, mensagem_erro|None).

    Etapas isoladas para que cada falha logue exatamente onde quebrou.
    """
    # ---- 1) Consulta Protheus -----------------------------------------------
    try:
        columns = [c.strip() for c in s.columns.split(",")] if s.columns else None
        filters = json.loads(s.filters) if s.filters else None
        result = protheus_api.query_table(
            table=s.table_name,
            columns=columns,
            filters=filters,
            page=1,
            page_size=5000,
        )
        rows = result["rows"]
    except ProtheusError as exc:
        logger.exception("Falha Protheus no agendamento #%s", s.id)
        return "error", f"[Protheus] {exc}"
    except json.JSONDecodeError as exc:
        logger.exception("Filtro JSON inválido no agendamento #%s", s.id)
        return "error", f"[Filtros JSON] {exc}"
    except Exception as exc:
        logger.exception("Falha inesperada (consulta) no agendamento #%s", s.id)
        return "error", f"[Consulta] {exc}"

    if not rows:
        # Geramos arquivo vazio mesmo assim — usuário pode querer saber.
        logger.info("Agendamento #%s gerou 0 linhas", s.id)

    # ---- 2) Geração do arquivo ----------------------------------------------
    try:
        ts = int(now_br().timestamp())
        path = generate(
            rows,
            s.file_format,
            base_name=f"{s.table_name}_{s.id}_{ts}",
        )
    except Exception as exc:
        logger.exception("Falha gerando arquivo no agendamento #%s", s.id)
        return "error", f"[Arquivo {s.file_format}] {exc}"

    # ---- 3) Envio SMTP (Sprint 5: HTML responsivo via send_report) ----------
    try:
        recipients = [e.strip() for e in s.recipients.split(",") if e.strip()]
        if not recipients:
            return "error", "[SMTP] Nenhum destinatário cadastrado"
        send_report(
            to=recipients,
            report_name=s.name,
            table=s.table_name,
            row_count=len(rows),
            period=now_br().strftime("%d/%m/%Y %H:%M (Brasília)"),
            file_format=s.file_format,
            attachment_name=path.name,
            attachments=[path],
            join_info="—",   # scheduler atual nao suporta joins ainda; placeholder
        )
    except Exception as exc:
        logger.exception("Falha SMTP no agendamento #%s", s.id)
        # Truncamos a mensagem para caber numa coluna Text sem virar wall-of-text.
        msg = str(exc).strip().replace("\n", " | ")[:800]
        return "error", f"[SMTP] {msg}"

    return "success", None


def tick() -> None:
    """Avalia todos os agendamentos. Chamado a cada intervalo do scheduler."""
    now = now_br()
    window = settings.SCHEDULER_INTERVAL_MINUTES
    db = SessionLocal()
    try:
        items = db.query(ScheduledReport).filter(ScheduledReport.is_active.is_(True)).all()
        for s in items:
            should_run = False
            if s.cron and _cron_matches_now(s.cron, now, window):
                should_run = True
            elif s.run_at and not s.last_run_at and s.run_at <= now.replace(tzinfo=None):
                should_run = True

            if not should_run:
                continue

            logger.info("Disparando agendamento #%s (%s)", s.id, s.name)
            try:
                status, err = _execute_schedule(s)
            except Exception as exc:
                # Cinto + suspensório: NADA escapa do worker.
                logger.exception("Exceção não tratada no agendamento #%s", s.id)
                status, err = "error", f"[Crítico] {exc}\n{traceback.format_exc()[-400:]}"

            s.last_run_at = _now_brt()
            s.last_status = status
            s.last_error = err
            db.add(AuditLog(
                username="(scheduler)",
                action="schedule.run",
                detail=f"Agendamento #{s.id} '{s.name}' -> {status}" + (f" | {err}" if err else ""),
                success=(status == "success"),
            ))
            db.commit()
    finally:
        db.close()


def fiscal_tick() -> None:
    """Enfileira uma auditoria fiscal automatica de D-1 para todas as filiais
    configuradas em `AppSetting('FISCAL_AUTO_BRANCHES')` (CSV).

    NAO executa inline — apenas chama o Celery. Se Redis estiver fora ou se
    FISCAL_AUTO_BRANCHES estiver vazio, loga e segue.
    """
    from datetime import date, timedelta as td
    from . import jobs as jobs_mod
    from .database import SessionLocal
    from .security import settings_store

    branches_csv = settings_store.get_setting("FISCAL_AUTO_BRANCHES", "")
    branches = [b.strip() for b in (branches_csv or "").split(",") if b.strip()]
    if not branches:
        logger.info("fiscal_tick: FISCAL_AUTO_BRANCHES vazio — pulando")
        return

    yesterday = (date.today() - td(days=1)).isoformat()
    payload = {
        "date_from": yesterday, "date_to": yesterday, "branches": branches,
        # Sprint 11 — flag para o run_audit enviar email mesmo com zero anomalias
        "autonomous_mode": True,
    }

    db = SessionLocal()
    try:
        job = jobs_mod.create_job("fiscal_audit", payload, db=db)
    finally:
        db.close()

    try:
        from .queue.tasks.fiscal_task import run_fiscal_audit
        async_result = run_fiscal_audit.apply_async(args=[job.id])
        jobs_mod._patch(job.id, celery_task_id=async_result.id)
        logger.info("fiscal_tick: job %s enfileirado (branches=%s, dia=%s)",
                    job.id, branches, yesterday)
    except Exception as exc:
        logger.warning("fiscal_tick: falha ao enfileirar (Redis off?): %s", exc)
        jobs_mod.mark_failed(job.id, "ERR-JOB-001", f"Fila indisponivel: {exc}")


# Sprint 11 — opcoes de cron para o Auditor Fiscal autonomo.
# Cada chave mapeia para um trigger APScheduler. Configuravel via
# AppSetting('FISCAL_AUTO_SCHEDULE'). Default: weekdays-6h (compat com versao anterior).
FISCAL_SCHEDULE_OPTIONS: dict[str, dict] = {
    "disabled":    {"label": "⏸ Desativado", "trigger": None},
    "every-3h":    {"label": "🕐 A cada 3 horas",  "trigger": ("interval", {"hours": 3})},
    "every-6h":    {"label": "🕓 A cada 6 horas",  "trigger": ("interval", {"hours": 6})},
    "every-12h":   {"label": "🕛 A cada 12 horas", "trigger": ("interval", {"hours": 12})},
    "daily-6h":    {"label": "🌅 Diariamente as 06:00",
                    "trigger": ("cron", {"hour": 6,  "minute": 0})},
    "daily-18h":   {"label": "🌇 Diariamente as 18:00",
                    "trigger": ("cron", {"hour": 18, "minute": 0})},
    "weekdays-6h": {"label": "🗓 Dias uteis as 06:00 (default)",
                    "trigger": ("cron", {"day_of_week": "mon-fri",
                                         "hour": 6, "minute": 0})},
}


def _build_fiscal_trigger(schedule_key: str):
    """Sprint 11 — retorna o APScheduler trigger para a opcao escolhida, ou
    None se 'disabled' / chave invalida."""
    opt = FISCAL_SCHEDULE_OPTIONS.get(schedule_key or "weekdays-6h")
    if not opt or not opt["trigger"]:
        return None
    kind, kwargs = opt["trigger"]
    if kind == "cron":
        return CronTrigger(timezone=TZ, **kwargs)
    if kind == "interval":
        return IntervalTrigger(**kwargs)
    return None


def _current_fiscal_schedule_key() -> str:
    """Le AppSetting('FISCAL_AUTO_SCHEDULE'). Default: 'weekdays-6h'."""
    try:
        from .security import settings_store
        return (settings_store.get_setting("FISCAL_AUTO_SCHEDULE", "weekdays-6h")
                or "weekdays-6h").strip()
    except Exception:
        return "weekdays-6h"


def reload_fiscal_schedule() -> dict:
    """Re-registra (ou remove) o job `fiscal_tick` conforme o setting atual.

    Chamado por:
    - `start()` no boot
    - `POST /api/admin/config/fiscal-schedule` apos o admin trocar a frequencia
    - `POST /api/admin/reload-config` (handler `_reload_config`)

    Retorna `{schedule_key, label, next_run_time}` para o caller exibir.
    """
    key = _current_fiscal_schedule_key()
    trig = _build_fiscal_trigger(key)
    # Remove o antigo se existir
    try:
        scheduler.remove_job("fiscal_tick")
    except Exception:
        pass

    if trig is None:
        logger.info("Auditor fiscal autonomo DESATIVADO (FISCAL_AUTO_SCHEDULE='%s')", key)
        return {"schedule_key": key, "label": FISCAL_SCHEDULE_OPTIONS.get(key, {}).get("label", key),
                "next_run_time": None, "enabled": False}

    scheduler.add_job(
        fiscal_tick,
        trigger=trig,
        id="fiscal_tick",
        replace_existing=True,
        misfire_grace_time=300,   # 5min de tolerancia se o worker estava down
    )
    # Pega o proximo fire-time
    job = scheduler.get_job("fiscal_tick")
    next_run = job.next_run_time.isoformat() if job and job.next_run_time else None
    label = FISCAL_SCHEDULE_OPTIONS.get(key, {}).get("label", key)
    logger.info("Auditor fiscal autonomo: %s (proximo: %s)", label, next_run)
    return {"schedule_key": key, "label": label,
            "next_run_time": next_run, "enabled": True}


def start() -> None:
    if scheduler.running:
        return
    # Tick principal: re-avalia agendamentos a cada N minutos.
    scheduler.add_job(
        tick,
        trigger=IntervalTrigger(minutes=settings.SCHEDULER_INTERVAL_MINUTES),
        id="reports_tick",
        replace_existing=True,
        next_run_time=now_br() + timedelta(seconds=15),
    )
    scheduler.start()
    # Sprint 11 — auditor fiscal autonomo com schedule configuravel
    reload_fiscal_schedule()
    logger.info("Scheduler iniciado — tick intervalo %s min",
                settings.SCHEDULER_INTERVAL_MINUTES)


def shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
