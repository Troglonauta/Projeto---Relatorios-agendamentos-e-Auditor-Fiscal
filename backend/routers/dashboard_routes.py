"""Dashboard remodelado (Fase 3 Sprint 3).

Endpoints:
- GET /api/dashboard/today          relatorios gerados hoje + jobs ativos + sparkline 7 dias
- GET /api/dashboard/fiscal-recent  ultimas auditorias fiscais (sucesso/anomalias)
- GET /api/dashboard/feed           timeline cronologica reversa: AuditLog + FiscalAnomaly + Jobs

Todos os endpoints exigem usuario logado (nao admin), mas operadores so veem
seus proprios jobs. Admins veem tudo.
"""
from __future__ import annotations

from datetime import datetime, timedelta, time
from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Date, func
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import AuditLog, FiscalAnomaly, Job, User
from ..timeutils import now_brt  # Ponto 5 (fuso): janelas do dia em BRT (dados sao BRT)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _today_bounds():
    """Retorna (start, end) do dia corrente em BRT (coerente com os dados)."""
    now = now_brt()
    start = datetime.combine(now.date(), time.min)
    end = start + timedelta(days=1)
    return start, end


# ---- /today ---------------------------------------------------------------

@router.get("/today")
def today(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    start, end = _today_bounds()

    # Jobs do dia (filtrados por owner se operador)
    q = db.query(Job).filter(Job.created_at >= start, Job.created_at < end)
    if user.role != "admin":
        q = q.filter(Job.owner_id == user.id)

    by_status = dict(
        q.with_entities(Job.status, func.count(Job.id)).group_by(Job.status).all()
    )
    total = sum(by_status.values())
    done = by_status.get("done", 0)
    failed = by_status.get("failed", 0)
    queued = by_status.get("queued", 0)
    running = by_status.get("running", 0)

    # Sparkline 7 dias: relatorios concluidos por dia
    spark = []
    for i in range(6, -1, -1):
        d_start = datetime.combine((now_brt() - timedelta(days=i)).date(), time.min)
        d_end = d_start + timedelta(days=1)
        cnt = (
            db.query(func.count(Job.id))
            .filter(Job.status == "done",
                    Job.created_at >= d_start, Job.created_at < d_end,
                    Job.kind == "report")
            .scalar() or 0
        )
        spark.append({"date": d_start.date().isoformat(), "count": cnt})

    return {
        "reports": {"total": total, "done": done, "failed": failed},
        "jobs":    {"queued": queued, "running": running},
        "sparkline_7d": spark,
    }


# ---- /fiscal-anomalies-histogram (Sprint 8) -------------------------------

@router.get("/fiscal-anomalies-histogram")
def fiscal_anomalies_histogram(
    days: int = Query(30, ge=1, le=180),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Sprint 8 — anomalias por dia nos ultimos N dias (default 30).

    Saida: lista ordenada cronologicamente (mais antigo primeiro), com
    contagem total + criticas separadas para o Chart.js renderizar barras
    empilhadas.
    """
    now = now_brt()
    end_day = datetime.combine(now.date(), time.min) + timedelta(days=1)
    start_day = end_day - timedelta(days=days)

    # 1 SELECT agrupado por dia, evita N+1
    day_expr = func.cast(FiscalAnomaly.audited_at, Date).label("d")
    rows = (
        db.query(day_expr, FiscalAnomaly.severity, func.count(FiscalAnomaly.id))
        .filter(FiscalAnomaly.severity != "ok",   # Fix #4 — ignora marcadores de doc OK
                FiscalAnomaly.audited_at >= start_day,
                FiscalAnomaly.audited_at < end_day)
        .group_by(day_expr, FiscalAnomaly.severity)
        .all()
    )

    # Sprint 20 Bug 3 — `d_val` pode vir como `date`, `datetime` OU string
    # dependendo do dialect (SQLite retorna str do CAST AS DATE; MSSQL retorna
    # date object). Antes: `d_val.isoformat() if hasattr(...) else str(d_val)`
    # mas ainda assim algo downstream chamava `fromisoformat(<datetime>)`
    # gerando `TypeError: fromisoformat: argument must be str`.
    # Solucao: sanitiza para `YYYY-MM-DD` SEMPRE, com fallback defensivo.
    def _safe_iso_date(v) -> str:
        if v is None:
            return ""
        # Caso 1: ja e' string — pode estar como "2026-05-31 00:00:00" ou
        # "2026-05-31" (SQLite). Pega so o prefixo de 10 chars.
        if isinstance(v, str):
            return v.strip()[:10]
        # Caso 2: datetime / date — chama isoformat() e corta em 10 chars.
        if hasattr(v, "isoformat"):
            try:
                return str(v.isoformat())[:10]
            except Exception:
                pass
        # Fallback final: str() + str()[:10]
        return str(v)[:10]

    # Constroi dict {date_iso: {total, critical, warn, info}}
    buckets: dict[str, dict[str, int]] = {}
    for d_val, sev, cnt in rows:
        key = _safe_iso_date(d_val)
        if not key:
            continue
        b = buckets.setdefault(key, {"total": 0, "critical": 0, "warn": 0, "info": 0})
        b["total"] += int(cnt)
        sev_key = (sev or "warn").lower()
        if sev_key in b:
            b[sev_key] += int(cnt)

    # Preenche dias sem anomalia com zero
    out = []
    for i in range(days - 1, -1, -1):
        d = (end_day - timedelta(days=i + 1)).date()
        key = d.isoformat()
        b = buckets.get(key, {"total": 0, "critical": 0, "warn": 0, "info": 0})
        out.append({
            "date": key,
            "total":    b["total"],
            "critical": b["critical"],
            "warn":     b["warn"],
            "info":     b["info"],
        })

    return {"days": days, "series": out}


# ---- /fiscal-recent --------------------------------------------------------

@router.get("/fiscal-recent")
def fiscal_recent(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Ultimas auditorias fiscais. Agrupa anomalias por job para ver
    se cada execucao terminou OK ou com divergencias."""
    audit_jobs = (
        db.query(Job)
        .filter(Job.kind == "fiscal_audit")
        .order_by(Job.created_at.desc())
        .limit(limit).all()
    )
    out = []
    for j in audit_jobs:
        anomaly_count = (
            db.query(func.count(FiscalAnomaly.id))
            .filter(FiscalAnomaly.job_id == j.id,
                    FiscalAnomaly.severity != "ok")   # Fix #4 — ignora marcadores OK
            .scalar() or 0
        )
        critical = (
            db.query(func.count(FiscalAnomaly.id))
            .filter(FiscalAnomaly.job_id == j.id,
                    FiscalAnomaly.severity == "critical")
            .scalar() or 0
        )
        out.append({
            "job_id": j.id,
            "status": j.status,
            "created_at": j.created_at.isoformat() if j.created_at else None,
            "finished_at": j.finished_at.isoformat() if j.finished_at else None,
            "rows_processed": j.rows_processed,
            "anomalies": anomaly_count,
            "critical": critical,
            "outcome": "ok" if anomaly_count == 0 and j.status == "done" else
                       "anomaly" if anomaly_count else j.status,
        })
    return {"items": out}


# ---- /feed -----------------------------------------------------------------

@router.get("/feed")
def feed(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Timeline cronologica reversa: jobs concluidos/falhos + anomalias fiscais.

    Operadores veem so jobs proprios. Anomalias so para admin.
    """
    items: list[dict] = []

    # 1) Jobs concluidos/falhos/cancelados
    q = db.query(Job).filter(Job.status.in_(("done", "failed", "canceled")))
    if user.role != "admin":
        q = q.filter(Job.owner_id == user.id)
    jobs = q.order_by(Job.finished_at.desc().nullslast(), Job.created_at.desc()).limit(limit).all()
    for j in jobs:
        items.append({
            "type": "job",
            "kind": j.kind,
            "id": j.id,
            "status": j.status,
            "ts": (j.finished_at or j.created_at).isoformat() if (j.finished_at or j.created_at) else None,
            "title": f"{'Relatorio' if j.kind == 'report' else 'Auditoria fiscal'} {j.status}",
            "detail": j.error_detail or f"{j.rows_processed or 0} linhas",
            "severity": "critical" if j.status == "failed" else "info",
        })

    # 2) Anomalias fiscais criticas (so admin)
    # Sprint 4.C: anomalias com ack/snooze NAO entram no feed (admin ja viu).
    if user.role == "admin":
        now_utc = now_brt()
        anomalies = (
            db.query(FiscalAnomaly)
            .filter(
                FiscalAnomaly.severity.in_(("critical", "warn")),
                FiscalAnomaly.acknowledged_at.is_(None),
                (FiscalAnomaly.snoozed_until.is_(None)) | (FiscalAnomaly.snoozed_until < now_utc),
            )
            .order_by(FiscalAnomaly.audited_at.desc())
            .limit(limit).all()
        )
        for a in anomalies:
            items.append({
                "type": "anomaly",
                "kind": "fiscal",
                "id": a.id,
                "ts": a.audited_at.isoformat() if a.audited_at else None,
                "title": f"Anomalia fiscal: {a.field_compared}",
                "detail": f"Doc ...{a.doc_key[-6:]} filial {a.branch} — {a.protheus_value} ≠ {a.xml_value}",
                "severity": a.severity,
            })

    items.sort(key=lambda x: x["ts"] or "", reverse=True)
    return {"items": items[:limit]}
