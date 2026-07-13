"""Endpoints do Auditor Fiscal — Sprint 12 (Auditoria Interna).

- POST /api/fiscal/audit/run  — enfileira job de auditoria interna SDS/SDT vs SF1/SD1.
- GET  /api/fiscal/anomalies  — lista anomalias com filtros (data, severidade, filial).
- GET  /api/fiscal/anomaly/{id} — detalhe de uma anomalia.
- GET  /api/fiscal/summary    — KPIs (usado pelo Dashboard).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import audit, jobs as jobs_mod
from ..database import get_db
from ..deps import get_client_ip, get_current_user, require_action
from ..models import FiscalAnomaly, FiscalDocumentReview, FiscalFieldDecision, User
from ..timeutils import now_brt

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/fiscal", tags=["fiscal"])


# ============================================================
#  Humanizacao da coluna "Campo" via SX3 (dicionario Protheus)
#  Mapeia a chave logica da regra -> campo FISICO do ERP, e busca o
#  X3_TITULO. Se a SX3 nao tiver, mantem o rotulo amigavel do motor.
# ============================================================
_FISCAL_HEADER_REF = {
    "numero_nota": "F1_DOC", "serie": "F1_SERIE", "data_emissao": "F1_EMISSAO",
    "data_digitacao": "F1_DTDIGIT", "especie": "F1_ESPECIE",
    "cnpj_fornecedor": "A2_CGC",
    "valor_mercadoria": "F1_VALMERC", "valor_total": "F1_VALBRUT",
    "frete": "F1_FRETE", "seguro": "F1_SEGURO", "desconto": "F1_DESCONT",
    "despesas": "F1_DESPESA",
}
_FISCAL_ITEM_REF = {
    "valor_unit": "D1_VUNIT", "quantidade": "D1_QUANT", "valor_total": "D1_TOTAL",
    "base_icms": "D1_BASEICM", "valor_icms": "D1_VALICM", "aliquota_icms": "D1_PICM",
    "cfop": "D1_CF", "cst": "D1_CLASFIS", "descricao": "D1_FSDPROD",
}


def _fiscal_field_ref(field_key: str) -> Optional[str]:
    """Campo FISICO do Protheus a partir da chave logica da regra."""
    if not field_key:
        return None
    if field_key.startswith("item_"):
        parts = field_key.split("_", 2)        # item_{n}_{base}
        return _FISCAL_ITEM_REF.get(parts[2]) if len(parts) > 2 else None
    return _FISCAL_HEADER_REF.get(field_key)


def _humanize_fiscal_label(field_key: str, fallback: str = "") -> tuple[str, Optional[str]]:
    """(titulo_humano, campo_fisico). Titulo vem da SX3 (so o titulo); se a SX3
    nao tiver o campo, usa o rotulo amigavel do motor (fallback)."""
    ref = _fiscal_field_ref(field_key)
    if not ref:
        return (fallback or field_key), None
    from .. import dict_sx3
    title = dict_sx3.humanize_title(ref)
    if title == ref:                            # SX3 sem titulo -> mantem fallback
        return (fallback or field_key), ref
    if field_key.startswith("item_"):
        n = field_key.split("_", 2)[1]
        return f"Item {n} — {title}", ref
    return title, ref


# ---- v2.26 — Catalogo FIXO de cruzamentos para o filtro do Auditor ----------
# Lista curada e estavel (sempre selecionavel), independente de ja ter divergido.
# `match` casa com `FiscalAnomaly.field_compared`: itens usam padrao "item_*_x"
# (qualquer item) para agrupar ICMS/CFOP/CST/etc. "Chave de acesso" = a nota foi
# lancada conforme a DANFE (sem marcador de Nota Ausente).
FISCAL_CROSS_CATALOG = [
    {"value": "chave_acesso",     "label": "Chave de acesso / Nota lançada (DANFE)", "match": ["nota_ausente"]},
    {"value": "numero_nota",      "label": "Número do documento",          "match": ["numero_nota"]},
    {"value": "serie",            "label": "Série",                        "match": ["serie"]},
    {"value": "data_emissao",     "label": "Data de emissão",              "match": ["data_emissao"]},
    {"value": "data_digitacao",   "label": "Data de digitação/importação", "match": ["data_digitacao"]},
    {"value": "especie",          "label": "Espécie",                      "match": ["especie"]},
    {"value": "cnpj_fornecedor",  "label": "CNPJ do fornecedor",           "match": ["cnpj_fornecedor"]},
    {"value": "icms",             "label": "ICMS (base / valor / alíquota)", "match": ["item_*_base_icms", "item_*_valor_icms", "item_*_aliquota_icms"]},
    {"value": "cfop",             "label": "CFOP",                         "match": ["item_*_cfop"]},
    {"value": "cst",              "label": "CST / Situação tributária",    "match": ["item_*_cst"]},
    {"value": "descricao",        "label": "Descrição do produto",         "match": ["item_*_descricao"]},
    {"value": "valor_mercadoria", "label": "Valor da mercadoria",          "match": ["valor_mercadoria"]},
    {"value": "valor_total",      "label": "Valor total da nota",          "match": ["valor_total"]},
    {"value": "frete",            "label": "Frete",                        "match": ["frete"]},
    {"value": "seguro",           "label": "Seguro",                       "match": ["seguro"]},
    {"value": "desconto",         "label": "Desconto",                     "match": ["desconto"]},
    {"value": "despesas",         "label": "Despesas acessórias",          "match": ["despesas"]},
    {"value": "quantidade",       "label": "Quantidade (item)",            "match": ["item_*_quantidade"]},
    {"value": "valor_unit",       "label": "Valor unitário (item)",        "match": ["item_*_valor_unit"]},
    {"value": "valor_total_item", "label": "Valor total (item)",           "match": ["item_*_valor_total"]},
    {"value": "item_ausente",     "label": "Item ausente (SD1 × XML)",     "match": ["item_*_ausente_xml", "item_*_ausente_sd1"]},
]


def _cross_match_clause(value: str):
    """Clausula SQLAlchemy (OR) sobre FiscalAnomaly.field_compared para o
    cruzamento escolhido. Suporta padrao 'item_*_x' (=> LIKE 'item_%_x')."""
    from sqlalchemy import or_
    entry = next((c for c in FISCAL_CROSS_CATALOG if c["value"] == value), None)
    if not entry:
        return FiscalAnomaly.field_compared == value   # fallback: valor cru
    conds = []
    for m in entry["match"]:
        if "*" in m:
            conds.append(FiscalAnomaly.field_compared.like(m.replace("*", "%")))
        else:
            conds.append(FiscalAnomaly.field_compared == m)
    return or_(*conds)


# ---- Schemas locais (Pydantic) -------------------------------------------

from pydantic import BaseModel, Field


class FiscalAuditRequest(BaseModel):
    """Sprint 12 — auditoria interna (SDS/SDT vs SF1/SD1).
    Sprint 20 — campo `engine` permite escolher o motor."""
    date_from: date
    date_to: date
    branches: List[str] = Field(min_length=1)
    # Busca por chave especifica (44 digitos). Ignora periodo.
    chave_filter: Optional[str] = None
    # Tipos de documento (modelos NFe: "55", "57", "65", etc). None/vazio = todos.
    doc_models: Optional[List[str]] = None
    # Sprint 20 — motor a usar:
    #   "internal"        (default) — SDS/SDT vs SF1/SD1 (motor fiscal padrao)
    #   "financeiro_se2"            — SF1 vs SE2 (Contas a Pagar)
    engine: Optional[str] = "internal"


# ---- Run audit ------------------------------------------------------------

@router.post("/audit/run", dependencies=[Depends(require_action("fiscal"))])
def run_audit(
    payload: FiscalAuditRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Enfileira um job de auditoria fiscal. Retorna 202 + job_id."""
    if payload.date_from > payload.date_to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "date_from deve ser <= date_to")
    if (payload.date_to - payload.date_from).days > 90:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Periodo maximo: 90 dias")

    # Validacao defensiva da chave
    chv = (payload.chave_filter or "").strip() or None
    if chv:
        if len(chv) != 44 or not chv.isdigit():
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "chave_filter deve ter 44 digitos numericos (chave de acesso da NFe)",
            )
    # Modelos validos (NFe usa 55/57/58/65/...).
    doc_models = None
    if payload.doc_models:
        cleaned = [m.strip() for m in payload.doc_models if m and m.strip()]
        if cleaned:
            for m in cleaned:
                if not m.isdigit() or len(m) != 2:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        f"doc_model invalido: {m!r} — use codigos de 2 digitos (55, 57, 58, 65)",
                    )
            doc_models = cleaned

    # Sprint 20+21 — valida motor solicitado
    engine_kind = (payload.engine or "internal").strip().lower()
    VALID_ENGINES = ("internal", "financeiro_se2", "comercial_sc5_se1")
    if engine_kind not in VALID_ENGINES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"engine invalido: '{engine_kind}'. Use um de: {', '.join(VALID_ENGINES)}",
        )

    job_payload = {
        "date_from": payload.date_from.isoformat(),
        "date_to": payload.date_to.isoformat(),
        "branches": payload.branches,
        "chave_filter": chv,
        "doc_models": doc_models,
        "engine": engine_kind,
    }
    job = jobs_mod.create_job("fiscal_audit", job_payload, owner_id=user.id, db=db)

    try:
        from ..queue.tasks.fiscal_task import run_fiscal_audit
        result = run_fiscal_audit.apply_async(args=[job.id])
    except Exception as exc:
        logger.exception("Falha ao enfileirar fiscal job %s", job.id)
        jobs_mod.mark_failed(job.id, "ERR-JOB-001", f"Fila indisponivel: {exc}")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Fila indisponivel: {exc}",
        )

    # A task ja foi despachada. Salvar o celery_task_id e a trilha e best-effort
    # (sessao propria / try): um lock momentaneo do SQLite nao pode virar 500.
    try:
        jobs_mod.set_celery_task_id(job.id, result.id)
    except Exception:
        logger.warning("Nao foi possivel salvar celery_task_id do job %s", job.id)
    try:
        audit.log(db, action="fiscal.audit.enqueue", user=user, ip=get_client_ip(request),
                  detail=f"job={job.id} period={payload.date_from}-{payload.date_to}")
    except Exception:
        try: db.rollback()
        except Exception: pass
        logger.warning("Nao foi possivel gravar a trilha do enqueue do job %s", job.id)
    return {"job_id": job.id, "status": "queued"}


# ============================================================
#  Sprint 20 Bug 2 — Purge destrutivo (Limpar Resultados real)
# ============================================================

@router.delete("/purge", dependencies=[Depends(require_action("fiscal"))])
def purge_anomalies(
    request: Request,
    include_jobs: bool = Query(False, description="Tambem apaga jobs fiscal_audit done"),
    older_than_days: Optional[int] = Query(None, ge=1, le=3650,
        description="So apaga registros mais antigos que N dias (None = todos)"),
    db: Session = Depends(get_db),
    admin=Depends(require_action("fiscal")),
):
    """Sprint 20 Bug 2 — Apaga FISICAMENTE registros de FiscalAnomaly (e
    opcionalmente Jobs fiscal_audit). O frontend chama este endpoint quando
    o usuario clica em "Limpar Resultados" — antes so limpava o DOM e as
    anomalias reapareciam ao recarregar.

    Acao destrutiva — registrada em AuditLog (`fiscal.purge`).
    """
    from ..models import Job

    q_anom = db.query(FiscalAnomaly)
    q_job = db.query(Job).filter(Job.kind == "fiscal_audit")
    if older_than_days is not None:
        cutoff = now_brt() - timedelta(days=older_than_days)
        q_anom = q_anom.filter(FiscalAnomaly.audited_at < cutoff)
        q_job = q_job.filter(Job.finished_at < cutoff)

    deleted_anomalies = q_anom.delete(synchronize_session=False)
    deleted_jobs = 0
    if include_jobs:
        deleted_jobs = q_job.delete(synchronize_session=False)
    db.commit()

    detail = (
        f"Purge fiscal: {deleted_anomalies} anomalia(s) apagada(s)" +
        (f", {deleted_jobs} job(s) apagado(s)" if include_jobs else "") +
        (f". Filtro: older_than_days={older_than_days}" if older_than_days else ". Filtro: TODOS")
    )
    audit.log(db, action="fiscal.purge", user=admin,
              ip=get_client_ip(request), detail=detail)
    return {
        "detail": "Limpeza concluida",
        "deleted_anomalies": deleted_anomalies,
        "deleted_jobs": deleted_jobs,
    }


# ---- Listagem -------------------------------------------------------------

@router.get("/anomalies", dependencies=[Depends(require_action("fiscal"))])
def list_anomalies(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    severity: Optional[str] = Query(None),
    branch: Optional[str] = Query(None),
    include_acked: bool = Query(False, description="Inclui ack/snoozed na resposta"),
    # Sprint 17 — toggle "Exibir apenas divergencias" (default True). Quando
    # True, exclui status='ok' (e severity='ok') do payload. A tabela
    # FiscalAnomaly so persiste 'divergent' por design (Sprint 13), entao
    # esse filtro e' defensivo — protege contra futuros backfills.
    only_errors: bool = Query(True, description="Filtra fora status='ok'/severity='ok'"),
    # Sprint 12 Hotfix: cap elevado p/ a UI paginar com DataTables. O filtro
    # de datas do usuario precisa trazer o periodo INTEIRO em uma chamada.
    limit: int = Query(100000, ge=1, le=100000),
    db: Session = Depends(get_db),
):
    q = db.query(FiscalAnomaly)
    if date_from:
        q = q.filter(FiscalAnomaly.audited_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        q = q.filter(FiscalAnomaly.audited_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))
    if severity:
        q = q.filter(FiscalAnomaly.severity == severity)
    if branch:
        q = q.filter(FiscalAnomaly.branch == branch)
    # Sprint 4.C — por padrao, esconde anomalias ack/snoozed
    if not include_acked:
        now = now_brt()  # Sprint 8 Part 2: BRT (coerente com audited_at)
        q = q.filter(FiscalAnomaly.acknowledged_at.is_(None))
        q = q.filter((FiscalAnomaly.snoozed_until.is_(None)) | (FiscalAnomaly.snoozed_until < now))
    # Sprint 17 — Toggle "Exibir apenas divergencias"
    if only_errors:
        q = q.filter(FiscalAnomaly.severity != "ok")
    items = q.order_by(FiscalAnomaly.audited_at.desc()).limit(limit).all()
    return [_to_dict(a) for a in items]


@router.get("/anomaly/{anomaly_id}", dependencies=[Depends(require_action("fiscal"))])
def get_anomaly(anomaly_id: int, db: Session = Depends(get_db)):
    a = db.query(FiscalAnomaly).filter(FiscalAnomaly.id == anomaly_id).first()
    if not a:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Anomalia nao encontrada")
    return _to_dict(a, full=True)


# ============================================================
#  Sprint 18 — Agrupado por documento (1 linha = 1 nota)
# ============================================================

@router.get("/grouped-anomalies", dependencies=[Depends(require_action("fiscal"))])
def list_grouped_anomalies(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    severity: Optional[str] = Query(None),
    branch: Optional[str] = Query(None),
    include_acked: bool = Query(False, description="Inclui ack/snoozed na agregacao"),
    only_errors: bool = Query(True, description="Filtra fora severity='ok'"),
    # v2.25 — filtro por CRUZAMENTO (campo) + situacao (divergente|conforme)
    field: Optional[str] = Query(None, description="field_compared do cruzamento"),
    field_status: Optional[str] = Query(None, description="divergent | ok (conforme)"),
    # v2.28 — drill-down por execucao de auditoria (decendio)
    job_id: Optional[str] = Query(None, description="documentos de uma auditoria especifica"),
    limit: int = Query(500, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Sprint 18 — Lista agrupada por documento (Mestre).

    Cada linha representa uma NF (doc_key + branch) com a contagem de
    divergencias. A UI principal usa este endpoint na tabela; o detalhamento
    dos campos divergentes vai no modal via `GET /document-audit`.

    Filtros sao os mesmos do `/anomalies` (data/severidade/filial/include_acked/
    only_errors). Paginacao via `limit`+`offset`.

    Retorno:
        {
          "total":  N,                 # total de DOCUMENTOS (nao de campos)
          "offset": ...,
          "limit":  ...,
          "items":  [
            {
              "doc_key": "44 digitos",
              "branch": "01",
              "supplier_cnpj": "...",
              "audited_at": "ISO datetime",
              "qtd_divergencias": 7,
              "qtd_critical":      3,
              "qtd_warn":          4,
            }, ...
          ],
        }
    """
    from sqlalchemy import func, case

    base_filters = []
    if date_from:
        base_filters.append(
            FiscalAnomaly.audited_at >= datetime.combine(date_from, datetime.min.time())
        )
    if date_to:
        base_filters.append(
            FiscalAnomaly.audited_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time())
        )
    if severity:
        base_filters.append(FiscalAnomaly.severity == severity)
    if branch:
        base_filters.append(FiscalAnomaly.branch == branch)
    if job_id:
        base_filters.append(FiscalAnomaly.job_id == job_id)
    if not include_acked:
        now = now_brt()
        base_filters.append(FiscalAnomaly.acknowledged_at.is_(None))
        base_filters.append(
            (FiscalAnomaly.snoozed_until.is_(None)) | (FiscalAnomaly.snoozed_until < now)
        )
    # Sprint 17 — herdado: filtro "exibir apenas divergencias"
    if only_errors:
        base_filters.append(FiscalAnomaly.severity != "ok")

    # v2.25 — filtro por CRUZAMENTO + situacao: seleciona DOCUMENTOS que tem (ou
    # NAO tem) divergencia no campo escolhido (ex: "chave/numero conforme x nao").
    if field:
        dsub = db.query(FiscalAnomaly.doc_key).filter(
            _cross_match_clause(field),
            FiscalAnomaly.severity != "ok",
        )
        if date_from:
            dsub = dsub.filter(FiscalAnomaly.audited_at >= datetime.combine(date_from, datetime.min.time()))
        if date_to:
            dsub = dsub.filter(FiscalAnomaly.audited_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))
        if branch:
            dsub = dsub.filter(FiscalAnomaly.branch == branch)
        dsub = dsub.distinct()
        if field_status == "ok":      # conforme: documentos SEM divergencia no campo
            base_filters.append(~FiscalAnomaly.doc_key.in_(dsub))
        else:                          # divergente (padrao quando ha campo)
            base_filters.append(FiscalAnomaly.doc_key.in_(dsub))

    q = db.query(
        FiscalAnomaly.doc_key.label("doc_key"),
        FiscalAnomaly.branch.label("branch"),
        func.max(FiscalAnomaly.audited_at).label("audited_at"),
        func.max(FiscalAnomaly.supplier_cnpj).label("supplier_cnpj"),
        # Fix #4 — qtd_divergencias conta APENAS severidades reais (ignora os
        # marcadores 'ok' de documentos auditados sem divergencia).
        func.sum(case((FiscalAnomaly.severity != "ok", 1), else_=0)).label("qtd_divergencias"),
        func.sum(case((FiscalAnomaly.severity == "critical", 1), else_=0)).label("qtd_critical"),
        func.sum(case((FiscalAnomaly.severity == "warn", 1), else_=0)).label("qtd_warn"),
    )
    if base_filters:
        q = q.filter(*base_filters)
    q = q.group_by(FiscalAnomaly.doc_key, FiscalAnomaly.branch)
    q = q.order_by(func.max(FiscalAnomaly.audited_at).desc())

    # Total = numero de grupos (subquery — funciona em SQLite e MSSQL)
    total = q.count()
    rows = q.offset(offset).limit(limit).all()

    items = []
    for r in rows:
        items.append({
            "doc_key":          r.doc_key or "",
            "branch":           r.branch or "",
            "supplier_cnpj":    r.supplier_cnpj or "",
            "audited_at":       r.audited_at.isoformat() if r.audited_at else None,
            "qtd_divergencias": int(r.qtd_divergencias or 0),
            "qtd_critical":     int(r.qtd_critical or 0),
            "qtd_warn":         int(r.qtd_warn or 0),
        })
    # v2.20 — anexa a revisao manual ("revisado por XXXX") visivel a todos.
    rmap = _reviews_map(db, [(it["doc_key"], it["branch"]) for it in items])
    for it in items:
        rv = rmap.get((it["doc_key"], it["branch"]))
        it["reviewed_by"] = rv["reviewed_by"] if rv else None
        it["reviewed_at"] = rv["reviewed_at"] if rv else None
    return {
        "total":  total,
        "offset": offset,
        "limit":  limit,
        "items":  items,
    }


@router.get("/anomaly-fields", dependencies=[Depends(require_action("fiscal"))])
def anomaly_fields(db: Session = Depends(get_db)):
    """v2.26 — Catalogo FIXO de cruzamentos (sempre selecionavel) para o filtro
    'por tipo de divergencia/conformidade'. Lista curada (chave de acesso, ICMS,
    CNPJ, emissao, numero do doc, etc.), independente de ter divergido."""
    return {"fields": [{"value": c["value"], "label": c["label"]} for c in FISCAL_CROSS_CATALOG]}


@router.get("/audit-runs", dependencies=[Depends(require_action("fiscal"))])
def audit_runs(limit: int = Query(60, ge=1, le=300), db: Session = Depends(get_db)):
    """v2.28 — Histórico de auditorias (DECÊNDIOS): cada execução do Auditor
    (Job kind='fiscal_audit') com período/filiais/motor/quando/quem + contagem de
    documentos e divergências (via FiscalAnomaly.job_id). Cada linha é gravada e
    fica registrada — a analista revisita o que auditou em cada decêndio."""
    import json
    from sqlalchemy import func, case, distinct
    from ..models import Job
    jobs = (
        db.query(Job).filter(Job.kind == "fiscal_audit")
        .order_by(Job.created_at.desc()).limit(limit).all()
    )
    if not jobs:
        return {"runs": []}
    job_ids = [j.id for j in jobs]
    rows = (
        db.query(
            FiscalAnomaly.job_id,
            func.count(distinct(FiscalAnomaly.doc_key)),
            func.sum(case((FiscalAnomaly.severity != "ok", 1), else_=0)),
        )
        .filter(FiscalAnomaly.job_id.in_(job_ids))
        .group_by(FiscalAnomaly.job_id).all()
    )
    cmap = {r[0]: {"docs": int(r[1] or 0), "divergencias": int(r[2] or 0)} for r in rows}
    owner_ids = {j.owner_id for j in jobs if j.owner_id}
    owners = {}
    if owner_ids:
        owners = {
            u.id: (u.full_name or u.username)
            for u in db.query(User).filter(User.id.in_(owner_ids)).all()
        }
    out = []
    for j in jobs:
        try:
            p = json.loads(j.payload_json or "{}")
        except Exception:
            p = {}
        c = cmap.get(j.id, {"docs": 0, "divergencias": 0})
        out.append({
            "job_id": j.id,
            "date_from": p.get("date_from"),
            "date_to": p.get("date_to"),
            "branches": p.get("branches") or [],
            "engine": p.get("engine") or "internal",
            "status": j.status,
            "created_at": j.created_at.isoformat() if j.created_at else None,
            "finished_at": j.finished_at.isoformat() if j.finished_at else None,
            "owner": owners.get(j.owner_id),
            "docs": c["docs"],
            "divergencias": c["divergencias"],
        })
    return {"runs": out}


def _reviews_map(db: Session, pairs: list) -> dict:
    """Mapa {(doc_key, branch): {reviewed_by, reviewed_at, note}} para os pares.

    Usado para anexar a revisao manual (v2.20) na lista mestre e no detalhe.
    """
    if not pairs:
        return {}
    doc_keys = list({p[0] for p in pairs if p[0]})
    if not doc_keys:
        return {}
    rows = (
        db.query(FiscalDocumentReview)
        .filter(FiscalDocumentReview.doc_key.in_(doc_keys))
        .all()
    )
    out = {}
    for r in rows:
        out[(r.doc_key, r.branch)] = {
            "reviewed_by": r.reviewed_by_name,
            "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
            "note": r.note,
        }
    return out


def _to_dict(a: FiscalAnomaly, full: bool = False) -> dict:
    d = {
        "id": a.id,
        "doc_key": a.doc_key,
        "branch": a.branch,
        "supplier_cnpj": a.supplier_cnpj,
        "field_compared": a.field_compared,
        "severity": a.severity,
        "audited_at": a.audited_at.isoformat() if a.audited_at else None,
        "sent_to_email": a.sent_to_email,
        "job_id": a.job_id,
        # Sprint 4.C — gestao de status
        "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None,
        "acknowledged_by_id": a.acknowledged_by_id,
        "ack_note": a.ack_note,
        "snoozed_until": a.snoozed_until.isoformat() if a.snoozed_until else None,
    }
    if full:
        d["protheus_value"] = a.protheus_value
        d["xml_value"] = a.xml_value
    return d


# ============================================================
#  Sprint 4.C — Ack / Snooze / Export
# ============================================================

@router.post("/anomaly/{anomaly_id}/ack", dependencies=[Depends(require_action("fiscal"))])
def ack_anomaly(
    anomaly_id: int, payload: dict, request: Request,
    db: Session = Depends(get_db), admin=Depends(require_action("fiscal")),
):
    """Marca anomalia como "ciente" OU adia (snooze).

    Body:
    - `{"note": "..."}` → ack imediato (some do Dashboard).
    - `{"snooze_days": 7, "note": "..."}` → silencia por N dias.
    """
    a = db.query(FiscalAnomaly).filter(FiscalAnomaly.id == anomaly_id).first()
    if not a:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Anomalia nao encontrada")

    note = (payload.get("note") or "").strip() or None
    snooze_days = payload.get("snooze_days")
    # Sprint 8 Part 2 — BRT (admin ve horario de Brasilia coerente com audited_at)
    now = now_brt()

    if snooze_days:
        try:
            d = int(snooze_days)
            if d < 1 or d > 365:
                raise ValueError("fora do range 1..365")
        except (TypeError, ValueError) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"snooze_days invalido: {exc}")
        a.snoozed_until = now + timedelta(days=d)
        a.acknowledged_at = None  # snooze nao e' ack definitivo
        action = "fiscal.anomaly.snooze"
        detail = f"id={a.id} days={d}"
    else:
        a.acknowledged_at = now
        a.snoozed_until = None
        action = "fiscal.anomaly.ack"
        detail = f"id={a.id}"

    a.acknowledged_by_id = admin.id
    if note:
        a.ack_note = note
    db.commit()
    audit.log(db, action=action, user=admin, ip=get_client_ip(request),
              detail=detail + (f" note={note[:80]}" if note else ""))
    return {"detail": "OK", "anomaly": _to_dict(a)}


# ============================================================
#  Sprint 19 — Ack EM MASSA por documento + trilha de decisao
# ============================================================

@router.post("/document/ack", dependencies=[Depends(require_action("fiscal"))])
def ack_document(
    payload: dict, request: Request,
    db: Session = Depends(get_db), admin=Depends(require_action("fiscal")),
):
    """Marca TODAS as divergencias de um documento (chave + filial) como
    cientes em uma unica acao.

    Body:
      {
        "doc_key": "44 digitos",
        "branch":  "01",
        "note":    "Justificacao/decisao do auditor (opcional)"
      }

    Registro obrigatorio na trilha de auditoria com action `fiscal.document.acked`
    e detalhe estruturado contendo a chave + justificativa — para a Direcao
    poder auditar quem aprovou cada divergencia e por que.
    """
    doc_key = (payload.get("doc_key") or "").strip()
    branch = (payload.get("branch") or "").strip()
    note = (payload.get("note") or "").strip()
    if not doc_key or not branch:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "doc_key e branch sao obrigatorios",
        )

    now = now_brt()
    # Atualiza somente as divergencias AINDA NAO ack/snooze
    q = db.query(FiscalAnomaly).filter(
        FiscalAnomaly.doc_key == doc_key,
        FiscalAnomaly.branch == branch,
        FiscalAnomaly.acknowledged_at.is_(None),
    )
    affected_rows = q.all()
    if not affected_rows:
        # Idempotente (v2.19.1): se o documento JA existe na base mas todas as
        # divergencias ja foram marcadas como cientes, re-clicar nao e erro —
        # apenas informa. So 404 quando o documento realmente nao existe (chave
        # desconhecida). O modal mostra divergencias via re-auditoria AO VIVO,
        # que ignora o ack, por isso o botao continua disponivel mesmo ja ciente.
        already = db.query(FiscalAnomaly.id).filter(
            FiscalAnomaly.doc_key == doc_key,
            FiscalAnomaly.branch == branch,
        ).first()
        if already:
            return {
                "detail": "Documento já estava marcado como ciente.",
                "doc_key": doc_key,
                "branch": branch,
                "affected": 0,
                "already_acked": True,
                "note": note or None,
            }
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Documento {doc_key[-8:]} (filial {branch}) nao encontrado na base de anomalias.",
        )

    for a in affected_rows:
        a.acknowledged_at = now
        a.acknowledged_by_id = admin.id
        a.snoozed_until = None
        if note:
            a.ack_note = note
    db.commit()

    # Trilha de auditoria — registro obrigatorio para a Direcao
    justificacao = note if note else "Sem justificacao"
    detail = (
        f"Documento {doc_key} marcado como ciente. "
        f"Justificacao/Decisao: {justificacao}. "
        f"Filial: {branch}. Divergencias afetadas: {len(affected_rows)}."
    )
    audit.log(
        db,
        action="fiscal.document.acked",
        user=admin,
        ip=get_client_ip(request),
        detail=detail,
    )

    return {
        "detail": f"Documento marcado como ciente — {len(affected_rows)} divergencia(s) ack'd",
        "doc_key": doc_key,
        "branch": branch,
        "affected": len(affected_rows),
        "note": note or None,
    }


@router.post("/document/review", dependencies=[Depends(require_action("fiscal"))])
def review_document(
    payload: dict, request: Request,
    db: Session = Depends(get_db), user=Depends(require_action("fiscal")),
):
    """v2.20 — Marca um DOCUMENTO como revisado manualmente (em conformidade).

    Diferente do ack (que da baixa nas divergencias armazenadas), este registro
    e' a nivel de documento e funciona SEMPRE — inclusive para Nota Ausente /
    documentos sem divergencia. Grava QUEM revisou e QUANDO, visivel a todos os
    usuarios ("revisado manualmente por XXXX"). Upsert por (doc_key, branch).

    Operador e admin podem revisar (acao `fiscal`). Fica registrado na trilha
    como `fiscal.document.reviewed`.
    """
    doc_key = (payload.get("doc_key") or "").strip()
    branch = (payload.get("branch") or "").strip()
    note = (payload.get("note") or "").strip()
    if not doc_key or not branch:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "doc_key e branch sao obrigatorios",
        )
    name = (getattr(user, "full_name", None) or user.username or "").strip()
    now = now_brt()
    rev = (
        db.query(FiscalDocumentReview)
        .filter(FiscalDocumentReview.doc_key == doc_key,
                FiscalDocumentReview.branch == branch)
        .first()
    )
    if rev:
        rev.reviewed_by_id = user.id
        rev.reviewed_by_name = name
        rev.reviewed_at = now
        rev.note = note or None
    else:
        rev = FiscalDocumentReview(
            doc_key=doc_key, branch=branch,
            reviewed_by_id=user.id, reviewed_by_name=name,
            reviewed_at=now, note=note or None,
        )
        db.add(rev)
    db.commit()

    detail = (
        f"Documento {doc_key} (filial {branch}) revisado manualmente por {name}."
        + (f" Nota: {note}" if note else "")
    )
    audit.log(
        db, action="fiscal.document.reviewed", user=user,
        ip=get_client_ip(request), detail=detail,
    )
    return {
        "detail": f"Documento revisado por {name}.",
        "doc_key": doc_key,
        "branch": branch,
        "reviewed_by": name,
        "reviewed_at": rev.reviewed_at.isoformat() if rev.reviewed_at else None,
        "note": note or None,
    }


@router.post("/field-decision", dependencies=[Depends(require_action("fiscal"))])
def field_decision(
    payload: dict, request: Request,
    db: Session = Depends(get_db), user=Depends(require_action("fiscal")),
):
    """v2.24 — Persiste a decisao manual de UM CAMPO (Conforme/Divergente/Sem
    dado) de um documento. Upsert por (doc_key, branch, field_key) — guarda quem
    alterou por ultimo. Cada chamada registra `fiscal.field.decided` na trilha
    (historico imutavel). NAO ha exclusao: so e' possivel ALTERAR.

    A re-auditoria ao vivo (document-audit) reaplica estas decisoes por cima do
    resultado do motor, entao a marcacao persiste mesmo apos novas auditorias.
    """
    doc_key = (payload.get("doc_key") or "").strip()
    branch = (payload.get("branch") or "").strip()
    field_key = (payload.get("field_key") or "").strip()
    new_status = (payload.get("status") or "").strip()
    note = (payload.get("note") or "").strip()
    if not doc_key or not branch or not field_key:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "doc_key, branch e field_key sao obrigatorios",
        )
    if new_status not in ("ok", "divergent", "skipped"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "status invalido (use ok|divergent|skipped)",
        )
    name = (getattr(user, "full_name", None) or user.username or "").strip()
    now = now_brt()
    dec = (
        db.query(FiscalFieldDecision)
        .filter(FiscalFieldDecision.doc_key == doc_key,
                FiscalFieldDecision.branch == branch,
                FiscalFieldDecision.field_key == field_key)
        .first()
    )
    prev = dec.status if dec else None
    if dec:
        dec.status = new_status
        dec.decided_by_id = user.id
        dec.decided_by_name = name
        dec.decided_at = now
        if note:
            dec.note = note
    else:
        dec = FiscalFieldDecision(
            doc_key=doc_key, branch=branch, field_key=field_key, status=new_status,
            decided_by_id=user.id, decided_by_name=name, decided_at=now, note=note or None,
        )
        db.add(dec)
    db.commit()
    audit.log(
        db, action="fiscal.field.decided", user=user, ip=get_client_ip(request),
        detail=(f"doc={doc_key} filial={branch} campo={field_key} "
                f"status={prev or '-'}->{new_status} por={name}"),
    )
    return {
        "detail": f"Campo atualizado por {name}.",
        "doc_key": doc_key, "branch": branch, "field_key": field_key,
        "status": new_status, "decided_by": name,
        "decided_at": dec.decided_at.isoformat() if dec.decided_at else None,
    }


@router.get("/decisions-summary", dependencies=[Depends(require_action("fiscal"))])
def decisions_summary(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    branch: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """v2.24 — Resumo das DECISOES MANUAIS por campo (painel separado do KPI do
    sistema). Permite saber o que foi ALTERADO manualmente e por quem, sem
    misturar com a auditoria automatica (FiscalAnomaly)."""
    q = db.query(FiscalFieldDecision)
    if date_from:
        q = q.filter(FiscalFieldDecision.decided_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        q = q.filter(FiscalFieldDecision.decided_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))
    if branch:
        q = q.filter(FiscalFieldDecision.branch == branch)

    rows = q.all()
    by_status = {"ok": 0, "divergent": 0, "skipped": 0}
    by_user: dict = {}
    docs: set = set()
    for r in rows:
        by_status[r.status] = by_status.get(r.status, 0) + 1
        by_user[r.decided_by_name] = by_user.get(r.decided_by_name, 0) + 1
        docs.add((r.doc_key, r.branch))
    by_user_list = sorted(
        ({"user": u, "count": c} for u, c in by_user.items()),
        key=lambda x: -x["count"],
    )
    recent = q.order_by(FiscalFieldDecision.decided_at.desc()).limit(limit).all()
    recent_out = [{
        "doc_key": d.doc_key, "branch": d.branch, "field_key": d.field_key,
        "status": d.status, "decided_by": d.decided_by_name,
        "decided_at": d.decided_at.isoformat() if d.decided_at else None,
        "note": d.note,
    } for d in recent]
    return {
        "total": len(rows),
        "by_status": by_status,
        "docs": len(docs),
        "by_user": by_user_list,
        "recent": recent_out,
    }


@router.post("/anomaly/{anomaly_id}/unack", dependencies=[Depends(require_action("fiscal"))])
def unack_anomaly(
    anomaly_id: int, request: Request,
    db: Session = Depends(get_db), admin=Depends(require_action("fiscal")),
):
    """Reverte ack/snooze — anomalia volta a aparecer no Dashboard."""
    a = db.query(FiscalAnomaly).filter(FiscalAnomaly.id == anomaly_id).first()
    if not a:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Anomalia nao encontrada")
    a.acknowledged_at = None
    a.acknowledged_by_id = None
    a.snoozed_until = None
    a.ack_note = None
    db.commit()
    audit.log(db, action="fiscal.anomaly.unack", user=admin, ip=get_client_ip(request),
              detail=f"id={a.id}")
    return {"detail": "OK", "anomaly": _to_dict(a)}


@router.get("/anomalies/export", dependencies=[Depends(require_action("fiscal"))])
def export_anomalies(
    fmt: str = Query("xlsx", pattern="^(csv|xlsx)$"),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    severity: Optional[str] = Query(None),
    branch: Optional[str] = Query(None),
    ncm_only: bool = Query(False),
    include_acked: bool = Query(False),
    # Sprint 17 — coerente com /anomalies: default True
    only_errors: bool = Query(True),
    db: Session = Depends(get_db),
):
    """Exporta as anomalias filtradas.

    Sprint 8 Part 2 (XLSX multi-aba):
    - Aba 1 "Anomalias Encontradas": warn + critical (severity != "pending")
    - Aba 2 "XMLs Pendentes na Fonte": somente severity = "pending" (xml_nao_encontrado)

    Header verde Fertimaxi + freeze panes A2 (mesma identidade do Sprint 5).

    Aplica os mesmos filtros do `/anomalies` mais:
    - `ncm_only`: so divergencias de NCM (compliance).
    - `include_acked`: por padrao FALSE — anomalias ack/snoozed nao entram.
    """
    from fastapi.responses import StreamingResponse
    import csv
    import io

    q = db.query(FiscalAnomaly)
    if date_from:
        q = q.filter(FiscalAnomaly.audited_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        q = q.filter(FiscalAnomaly.audited_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))
    if severity:
        q = q.filter(FiscalAnomaly.severity == severity)
    if branch:
        q = q.filter(FiscalAnomaly.branch == branch)
    if not include_acked:
        now = now_brt()
        q = q.filter(FiscalAnomaly.acknowledged_at.is_(None))
        q = q.filter((FiscalAnomaly.snoozed_until.is_(None)) | (FiscalAnomaly.snoozed_until < now))
    # Sprint 17 — coerente com /anomalies
    if only_errors:
        q = q.filter(FiscalAnomaly.severity != "ok")
    all_rows = q.order_by(FiscalAnomaly.audited_at.desc()).all()
    if ncm_only:
        all_rows = [r for r in all_rows if "ncm" in (r.field_compared or "").lower()]

    # Sprint 15 — colunas do relatorio executivo de anomalias.
    cols = [
        "ID", "Auditado em (BRT)", "Chave NFe", "Filial", "Fornecedor",
        "Campo", "Valor Protheus (SF1/SD1)", "Valor XML (SDS/SDT)", "Severidade",
        "Reconhecido em", "Snooze ate", "Observacao",
    ]

    def _row(r: FiscalAnomaly) -> dict:
        return {
            "ID": r.id,
            "Auditado em (BRT)": r.audited_at.strftime("%d/%m/%Y %H:%M") if r.audited_at else "",
            "Chave NFe": r.doc_key,
            "Filial": r.branch,
            "Fornecedor": r.supplier_cnpj or "",
            "Campo": _humanize_fiscal_label(r.field_compared, r.field_compared)[0],
            "Valor Protheus (SF1/SD1)": r.protheus_value or "",
            "Valor XML (SDS/SDT)": r.xml_value or "",
            "Severidade": (r.severity or "").upper(),
            "Reconhecido em": r.acknowledged_at.strftime("%d/%m/%Y %H:%M") if r.acknowledged_at else "",
            "Snooze ate": r.snoozed_until.strftime("%d/%m/%Y %H:%M") if r.snoozed_until else "",
            "Observacao": r.ack_note or "",
        }

    ts = now_brt().strftime("%Y%m%d_%H%M%S")

    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        w.writerow(cols)
        for r in all_rows:
            row_dict = _row(r)
            w.writerow([row_dict[c] for c in cols])
        data = ("﻿" + buf.getvalue()).encode("utf-8")
        return StreamingResponse(
            iter([data]), media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="anomalias_{ts}.csv"'},
        )

    # Sprint 15 — XLSX formatado via helper compartilhado (header verde
    # centralizado, AutoFit, freeze panes, autofilter).
    from ..xlsx_utils import build_formatted_xlsx_bytes
    blob = build_formatted_xlsx_bytes(
        [_row(r) for r in all_rows],
        sheet_name="Anomalias",
        columns=cols,
        reorder=False,   # colunas fixas — ja estao na ordem desejada
    )
    return StreamingResponse(
        iter([blob]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="anomalias_{ts}.xlsx"'},
    )


# ---- Summary (Dashboard) --------------------------------------------------

@router.get("/summary")
def summary(
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """KPIs do Auditor Fiscal nos ultimos N dias.

    Sprint 19: paradigma orientado a DOCUMENTOS (Mestre):
      - total_docs_audited: soma de `rows_processed` em Jobs fiscal_audit done
      - docs_with_errors: distinct doc_key em FiscalAnomaly (nao-ack) no periodo
      - docs_ok: max(0, total_docs_audited - docs_with_errors)

    Sprint 4.C (legado): anomalias com `acknowledged_at` ou `snoozed_until > now`
    NAO contam — admin ja decidiu. Campos antigos (total/critical/warn/info/
    nota_ausente/by_branch/acked) sao mantidos para retro-compat com clientes
    legados do endpoint (Dashboard atual ja usa os novos).
    """
    from ..models import Job
    # Sprint 8 Part 2 — BRT (audited_at agora e' BRT, comparacao coerente)
    now = now_brt()
    since = now - timedelta(days=days)
    base = db.query(FiscalAnomaly).filter(
        FiscalAnomaly.severity != "ok",   # Fix #4 — ignora marcadores de doc OK
        FiscalAnomaly.audited_at >= since,
        FiscalAnomaly.acknowledged_at.is_(None),
        (FiscalAnomaly.snoozed_until.is_(None)) | (FiscalAnomaly.snoozed_until < now),
    )
    total = base.count()
    critical = base.filter(FiscalAnomaly.severity == "critical").count()
    warn = base.filter(FiscalAnomaly.severity == "warn").count()
    info = total - critical - warn
    nota_ausente = base.filter(FiscalAnomaly.field_compared == "nota_ausente").count()
    by_branch = dict(
        base.with_entities(FiscalAnomaly.branch, func.count(FiscalAnomaly.id))
        .group_by(FiscalAnomaly.branch).all()
    )
    acked = db.query(FiscalAnomaly).filter(
        FiscalAnomaly.audited_at >= since,
        FiscalAnomaly.acknowledged_at.isnot(None),
    ).count()

    # Sprint 19 — KPIs orientados a documentos
    # 1) Notas auditadas: soma dos `rows_processed` dos jobs fiscal_audit done.
    #    Cada job grava docs_audited em `rows_processed` (fiscal_task.mark_done).
    docs_audited_sum = db.query(func.coalesce(func.sum(Job.rows_processed), 0)).filter(
        Job.kind == "fiscal_audit",
        Job.status == "done",
        Job.finished_at.isnot(None),
        Job.finished_at >= since,
    ).scalar() or 0
    # 2) Notas com pelo menos 1 divergencia (pendente)
    docs_with_errors = db.query(func.count(func.distinct(FiscalAnomaly.doc_key))).filter(
        FiscalAnomaly.severity != "ok",   # Fix #4 — so docs com divergencia real
        FiscalAnomaly.audited_at >= since,
        FiscalAnomaly.acknowledged_at.is_(None),
        (FiscalAnomaly.snoozed_until.is_(None)) | (FiscalAnomaly.snoozed_until < now),
    ).scalar() or 0
    # 3) Notas OK = auditadas - com erros (chao em 0; pode ser que jobs nao tenham
    #    persistido rows_processed em execucoes antigas)
    docs_ok = max(0, int(docs_audited_sum) - int(docs_with_errors))

    return {
        "days": days,
        # Sprint 19 — KPIs orientados a documentos (principais)
        "total_docs_audited": int(docs_audited_sum),
        "docs_with_errors": int(docs_with_errors),
        "docs_ok": docs_ok,
        # Retro-compat — campos antigos (Sprint 4.C / 8.2 / 12)
        "total": total,
        "critical": critical,
        "warn": warn,
        "info": info,
        "nota_ausente": nota_ausente,
        "by_branch": by_branch,
        "acked": acked,
    }


# ---- Sprint 12 — info do motor interno -----------------------------------

@router.get("/engine-info")
def engine_info(user: User = Depends(get_current_user)):
    """Retorna informacoes do motor de auditoria.

    Sprint 12: substitui o antigo `/source-info` (que reportava fonte XML
    externa). Agora o motor e' fixo (interno SDS/SDT vs SF1/SD1) e este
    endpoint serve apenas para a UI exibir as tolerancias atuais.
    """
    from ..fiscal import comparators
    from ..security import settings_store

    return {
        "engine":      "internal-audit",
        "engine_label":"Auditoria Interna (SDS/SDT vs SF1/SD1)",
        "tolerance": {
            "valor_rs":   str(comparators.tol_valor()),
            "icms_rs":    str(comparators.tol_icms()),
            "quantidade": str(comparators.tol_qtd()),
        },
        "ncm_validation": str(
            settings_store.get_setting("FISCAL_VALIDATE_NCM", "true")
        ).lower() in ("1", "true", "yes"),
    }


# ---- Sprint 13 — Relatorio Completo de Documento --------------------------

@router.get("/document-audit", dependencies=[Depends(require_action("fiscal"))])
def document_audit(
    branch: str = Query(..., min_length=1, max_length=4),
    # Fix #4 — aceita a chave NFe (44 digitos) OU um doc_key sintetico
    # "doc/serie/fornec/loja" (NF avulsa/CTe sem chave). Por isso sem min/max aqui.
    chave: Optional[str] = Query(None, max_length=80,
                                  description="Chave NFe (44 dig) ou doc_key 'doc/serie/fornec/loja'"),
    # Sprint 17 — toggle "Exibir apenas divergencias" (default True).
    # Quando True, filtra fora `status='ok'` do array `report` (mantem
    # divergent + skipped). `counts` continua refletindo TODOS os campos
    # auditados, independente do filtro.
    only_errors: bool = Query(True, description="Remove status='ok' do report"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Auditoria COMPLETA de um documento — retorna TODOS os campos com
    status `ok`/`divergent`/`skipped`. NAO persiste em FiscalAnomaly.

    Sprint 13: usado pela UI "Relatorio Completo" para apresentacao executiva.
    Sprint 17: aceita `only_errors` para filtrar campos com status='ok' no
    array `report` (a contagem completa em `counts` e' preservada).
    Exige a chave de acesso para localizar o doc deterministicamente em SDS.
    """
    from datetime import date as _date
    from .. import protheus_api
    from ..fiscal.internal_audit import _load_audit_period_internal
    from ..fiscal.rule_engine import FiscalRuleEngine

    if not chave:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Informe a chave NFe (44 digitos) ou o doc_key para o relatorio completo",
        )
    chave_clean = chave.strip()

    # Fix #4 — roteia: chave de 44 digitos -> busca por DS_CHAVENF; senao,
    # interpreta como doc_key sintetico "doc/serie/fornec/loja".
    chave_filter = None
    doc_filter = None
    if len(chave_clean) == 44 and chave_clean.isdigit():
        chave_filter = chave_clean
    else:
        parts = chave_clean.split("/")
        if not parts or not parts[0].strip():
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Identificador invalido — use a chave (44 digitos) ou doc/serie/fornec/loja",
            )
        doc_filter = {
            "doc":    parts[0].strip() if len(parts) > 0 else "",
            "serie":  parts[1].strip() if len(parts) > 1 else "",
            "fornec": parts[2].strip() if len(parts) > 2 else "",
            "loja":   parts[3].strip() if len(parts) > 3 else "",
        }

    engine = protheus_api.engine_registry.get()
    # date_from/to ignorados quando ha chave_filter/doc_filter
    today = _date.today()
    docs = _load_audit_period_internal(
        engine, branch, today, today,
        chave_filter=chave_filter, doc_filter=doc_filter,
    )
    if not docs:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Documento nao encontrado em SDS{branch} ({chave_clean[-12:]})",
        )

    doc = docs[0]
    full_rep = FiscalRuleEngine(doc).run()
    # v2.24 — sobrepoe as DECISOES MANUAIS por campo (persistidas) ao resultado
    # do motor, ANTES de contar — assim os contadores e o filtro ja refletem as
    # marcacoes da analista, e elas sobrevivem a novas auditorias.
    def _fkey(r: dict) -> str:
        return f"{r.get('item_n') or 'H'}:{r.get('field', '')}"
    decisions = {
        d.field_key: d
        for d in db.query(FiscalFieldDecision).filter(
            FiscalFieldDecision.doc_key == chave_clean,
            FiscalFieldDecision.branch == branch,
        ).all()
    }
    for r in full_rep:
        fk = _fkey(r)
        r["field_key"] = fk
        d = decisions.get(fk)
        if d:
            r["status"] = d.status
            r["decided_by"] = d.decided_by_name
            r["decided_at"] = d.decided_at.isoformat() if d.decided_at else None
            r["manual"] = True
    # `counts` SEMPRE reflete o universo completo (mesmo com only_errors=True),
    # para a UI mostrar "X de Y campos com divergencia" no sumario.
    counts = {
        "total": len(full_rep),
        "ok": sum(1 for r in full_rep if r["status"] == "ok"),
        "divergent": sum(1 for r in full_rep if r["status"] == "divergent"),
        "skipped": sum(1 for r in full_rep if r["status"] == "skipped"),
    }
    # Humaniza a coluna "Campo" via SX3 (so o titulo) + guarda o campo fisico
    # em `field_ref` para o tooltip no frontend.
    for r in full_rep:
        title, ref = _humanize_fiscal_label(r.get("field", ""), r.get("label", ""))
        r["label"] = title
        r["field_ref"] = ref
    # Sprint 17 — Toggle "Exibir apenas divergencias"
    if only_errors:
        rep = [r for r in full_rep if r.get("status") != "ok"]
    else:
        rep = full_rep
    counts["filtered"] = len(rep)
    counts["only_errors"] = only_errors
    sds = doc.get("sds") or {}
    sf1 = doc.get("sf1") or {}
    sa2 = doc.get("sa2") or {}
    summary = {
        "chave": doc.get("chave"),
        "branch": doc.get("branch"),
        "ds_doc": (sds.get("DS_DOC") or "").strip() if isinstance(sds.get("DS_DOC"), str) else sds.get("DS_DOC"),
        "ds_serie": ((str(sds.get("DS_SERIE") or "").strip() or str(sds.get("DS_SDOC") or "").strip()) or None),
        "ds_emissa": (sds.get("DS_EMISSA") or "").strip() if isinstance(sds.get("DS_EMISSA"), str) else sds.get("DS_EMISSA"),
        # Data de digitacao/classificacao no ERP (destaque no header) e a data de
        # importacao do XML (SDS), para a analista comparar emissao x digitacao.
        "f1_dtdigit": (str(sf1.get("F1_DTDIGIT") or "").strip() or None) if sf1 else None,
        "ds_dataimp": (str(sds.get("DS_DATAIMP") or "").strip() or None),
        "f1_especie": (str(sf1.get("F1_ESPECIE") or "").strip() or None) if sf1 else None,
        "ds_especi": (str(sds.get("DS_ESPECI") or "").strip() or None),
        "fornecedor": (sa2.get("A2_NOME") or "").strip() if sa2 and isinstance(sa2.get("A2_NOME"), str) else None,
        "nota_classificada": bool(sf1),
        "f1_doc": (sf1.get("F1_DOC") or "").strip() if sf1 and isinstance(sf1.get("F1_DOC"), str) else None,
        "items_sdt": len(doc.get("sdt_items") or []),
        "items_sd1": len(doc.get("sd1_items") or []),
    }
    # v2.20 — revisao manual do documento (se houver), visivel a todos.
    review = _reviews_map(db, [(chave_clean, branch)]).get((chave_clean, branch))
    return {
        "summary": summary,
        "counts": counts,
        "report": rep,
        "review": review,
    }
