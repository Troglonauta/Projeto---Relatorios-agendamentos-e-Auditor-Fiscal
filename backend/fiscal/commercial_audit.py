"""Commercial Audit Engine — Sprint 21 (Garantia de Receita).

Cruzamento Comercial / Faturamento:
  SC5 (Pedidos de Venda já faturados) ⚔ SE1 (Títulos do Contas a Receber)

Condicao de JOIN (definida pelo cliente):
  SC5.C5_NOTA    = SE1.E1_NUM
  AND SC5.C5_CLIENTE = SE1.E1_CLIENTE
  AND SC5.C5_LOJACLI = SE1.E1_LOJA

Filtro CRITICO da base SC5:
  - C5_NOTA <> ' '   (so pedidos que JA viraram nota fiscal)
  - C5_EMISSAO BETWEEN :df AND :dt

Regras aplicadas (sempre prefixadas `com_*` ao persistir em FiscalAnomaly):
  R0 — Ausencia de Titulo      (CRITICAL):  C5_NOTA preenchido mas SE1 None
                                           -> faturamento sem receita registrada
  R1 — Fraude/Erro de Vendedor (CRITICAL):  C5_VEND1 vs E1_VEND1 — comissoes
                                           podem estar sendo repassadas para o
                                           vendedor errado.
  R2 — Prazo de pagamento      (WARN)    :  C5_DATA1 (vencimento prometido) vs
                                           E1_VENCREA (ou E1_VENCTO) — detecta
                                           se o faturamento "afrouxou" o prazo
                                           negociado pelo comercial.
  R3 — Desconto financeiro     (WARN)    :  C5_DESCFI (ou C5_DESC1) vs
                                           E1_DESCONT — desvio na concessao.

Notas:
- SE1 pode ter MULTIPLAS PARCELAS por NF (E1_PARCELA). Para R2/R3 usamos a
  parcela 1 (E1_PARCELA = '1' ou '01' ou vazia) — bate com C5_DATA1/C5_DESCFI
  que sao da primeira parcela do pedido. Para R1 a comparacao usa qualquer
  parcela: se TODAS divergirem do C5_VEND1 e' uma divergencia critica unica
  (registramos pelo primeiro mismatch).
- Pedidos com TIPO=B (bonificacao) sao IGNORADOS na regra R0 (skip silencioso).
  Configuravel via AppSetting `COMMERCIAL_SKIP_TIPOS` (CSV).
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy import text

from ..security import settings_store
from .internal_audit import (
    _is_missing_table_or_col,
    _resolve_branch,
    _safe_get,
    _t,
)
from . import comparators

logger = logging.getLogger(__name__)


# ============================================================
#  Colunas oficiais SC5/SE1 (Protheus padrao)
# ============================================================

SC5_COLS = (
    "C5_FILIAL, C5_NUM, C5_TIPO, C5_CLIENTE, C5_LOJACLI, "
    "C5_EMISSAO, C5_NOTA, C5_SERIE, C5_VEND1, "
    "C5_DATA1, C5_CONDPAG, C5_DESCFI, C5_DESC1"
)
SE1_COLS = (
    "E1_FILIAL, E1_PREFIXO, E1_NUM, E1_PARCELA, E1_TIPO, "
    "E1_CLIENTE, E1_LOJA, E1_EMISSAO, E1_VENCTO, E1_VENCREA, "
    "E1_VALOR, E1_SALDO, E1_VEND1, E1_DESCONT"
)

# TIPOS de pedido que NAO geram receita / titulo (skip R0)
_DEFAULT_SKIP_TIPOS = {"B", "D"}   # B=bonificacao, D=devolucao


def _skip_tipos() -> set[str]:
    raw = settings_store.get_setting("COMMERCIAL_SKIP_TIPOS", "")
    if raw:
        return {t.strip().upper() for t in str(raw).split(",") if t.strip()}
    return set(_DEFAULT_SKIP_TIPOS)


def _to_decimal(v) -> Optional[Decimal]:
    if v is None or str(v).strip() == "":
        return None
    try:
        return Decimal(str(v).strip().replace(",", ".")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _norm_parcela(p: str) -> str:
    """'1' / '01' / ' ' -> '1' (canonico)."""
    return (p or "").strip().lstrip("0") or "1"


# ============================================================
#  Loader: SC5 LEFT JOIN SE1 (com agregacao por NF)
# ============================================================

def _load_commercial_audit_period(
    db_engine,
    branch: str,
    date_from: date,
    date_to: date,
) -> list[dict]:
    """Carrega o periodo do MOTOR COMERCIAL em batch.

    Estrategia:
    1. SELECT SC5 do periodo FILTRANDO pedidos JA FATURADOS (C5_NOTA <> ' ').
    2. LEFT JOIN SE1 por (C5_NOTA=E1_NUM, C5_CLIENTE=E1_CLIENTE, C5_LOJACLI=E1_LOJA).
    3. Agrupa SE1 por (NUM, CLIENTE, LOJA) -> lista de parcelas.

    Retorna lista de dicts:
        {
          "doc_key": "C5_NUM|C5_NOTA|C5_CLIENTE|C5_LOJACLI",
          "branch":  "01",
          "sc5":     {C5_*: valor, ...},
          "se1":     {parcelas: [{E1_*: ...}], parc1: {...}|None} | None,
        }
    """
    suffix = settings_store.get_setting("PROTHEUS_TABLE_SUFFIX", "0")
    sc5_tbl = _t("SC5", branch, suffix)
    se1_tbl = _t("SE1", branch, suffix)
    _, filial_filter = _resolve_branch(branch)

    df = date_from.strftime("%Y%m%d")
    dt = date_to.strftime("%Y%m%d")

    sc5_cols_q = ", ".join(f"sc5.{c.strip()}" for c in SC5_COLS.split(","))
    se1_cols_q = ", ".join(f"se1.{c.strip()}" for c in SE1_COLS.split(","))

    filial_clause = " AND sc5.C5_FILIAL = :filial" if filial_filter else ""
    sql = (
        f"SELECT {sc5_cols_q}, {se1_cols_q} "
        f"FROM {sc5_tbl} sc5 WITH (NOLOCK) "
        f"LEFT JOIN {se1_tbl} se1 WITH (NOLOCK) "
        f"  ON se1.D_E_L_E_T_ = ' ' "
        f"  AND se1.E1_NUM     = sc5.C5_NOTA "
        f"  AND se1.E1_CLIENTE = sc5.C5_CLIENTE "
        f"  AND se1.E1_LOJA    = sc5.C5_LOJACLI "
        f"WHERE sc5.D_E_L_E_T_ = ' ' "
        f"  AND sc5.C5_NOTA <> ' ' "                  # so pedidos JA faturados
        f"  AND sc5.C5_EMISSAO BETWEEN :df AND :dt"
        + filial_clause + " "
        f"ORDER BY sc5.C5_EMISSAO, sc5.C5_NUM, se1.E1_PARCELA"
    )
    params: dict = {"df": df, "dt": dt}
    if filial_filter:
        params["filial"] = filial_filter

    sc5_keys = [c.strip() for c in SC5_COLS.split(",")]
    se1_keys = [c.strip() for c in SE1_COLS.split(",")]

    try:
        with db_engine.connect() as conn:
            rows = conn.execute(text(sql), params).all()
    except Exception as exc:
        if _is_missing_table_or_col(exc):
            logger.warning(
                "SC5/SE1 (%s/%s) — colunas inexistentes nesta release: %s",
                sc5_tbl, se1_tbl, exc,
            )
            return []
        raise

    aggregated: dict[tuple, dict] = {}   # (NUM, CLIENTE, LOJA) -> doc
    for r in rows:
        m = dict(r._mapping)
        sc5 = {k: m.get(k) for k in sc5_keys}
        key = (
            _safe_get(sc5, "C5_NUM"),
            _safe_get(sc5, "C5_NOTA"),
            _safe_get(sc5, "C5_CLIENTE"),
            _safe_get(sc5, "C5_LOJACLI"),
        )
        if not key[0] or not key[1]:
            continue

        doc = aggregated.get(key)
        if doc is None:
            doc = {
                "doc_key": "|".join(key),
                "branch": branch,
                "sc5": sc5,
                "se1": None,
            }
            aggregated[key] = doc

        e1_num = _safe_get(m, "E1_NUM")
        if not e1_num:
            continue   # LEFT JOIN sem match — Ausencia de Titulo

        se1_payload = doc["se1"]
        if se1_payload is None:
            se1_payload = {"parcelas": [], "parc1": None}
            doc["se1"] = se1_payload

        parc = {k: m.get(k) for k in se1_keys}
        se1_payload["parcelas"].append(parc)
        # Identifica a parcela "1" (canonico) para R2/R3
        if _norm_parcela(_safe_get(parc, "E1_PARCELA")) == "1":
            if se1_payload["parc1"] is None:
                se1_payload["parc1"] = parc

    docs = list(aggregated.values())
    sem_titulo = sum(1 for d in docs if not d["se1"])
    logger.info(
        "CommercialAudit %s: %d pedidos faturados, %d sem SE1 (Ausencia de Titulo)",
        branch, len(docs), sem_titulo,
    )
    return docs


# ============================================================
#  Engine de regras
# ============================================================

def audit_commercial_doc(doc: dict) -> list[dict]:
    """Aplica as 4 regras comerciais em um doc agregado.

    Retorna lista de items com schema persistivel:
        {field, label, protheus_value, xml_value, status, severity, note,
         category="commercial", item_n=None}
    """
    out: list[dict] = []
    sc5 = doc.get("sc5") or {}
    se1 = doc.get("se1")
    c5_num   = _safe_get(sc5, "C5_NUM")
    c5_nota  = _safe_get(sc5, "C5_NOTA")
    c5_tipo  = _safe_get(sc5, "C5_TIPO").upper()
    c5_vend1 = _safe_get(sc5, "C5_VEND1")
    c5_data1 = _safe_get(sc5, "C5_DATA1")
    # C5_DESCFI tem prioridade; C5_DESC1 e' fallback
    c5_desc = _to_decimal(sc5.get("C5_DESCFI"))
    if c5_desc is None:
        c5_desc = _to_decimal(sc5.get("C5_DESC1"))

    # ---- R0: Ausencia de Titulo (CRITICAL) ----
    if se1 is None:
        if c5_tipo and c5_tipo in _skip_tipos():
            return out   # bonificacao/devolucao nao gera receita — esperado
        out.append({
            "field": "com_ausencia_titulo",
            "label": "Ausência de Título (Pedido faturado sem SE1)",
            "category": "commercial",
            "item_n": None,
            "protheus_value": f"Pedido {c5_num} -> NF {c5_nota} (TIPO={c5_tipo or '?'})",
            "xml_value": "(sem registro em SE1)",
            "status": "divergent",
            "severity": "critical",
            "note": "Pedido SC5 faturado (C5_NOTA preenchido) mas nao gerou titulo no Contas a Receber",
        })
        return out

    parc1 = se1.get("parc1")
    parcelas = se1.get("parcelas") or []

    # ---- R1: Vendedor (CRITICAL) ----
    # Compara C5_VEND1 com E1_VEND1 da parcela 1 (ou da primeira parcela
    # se nao houver parc1). Se TODAS as parcelas divergem, registra UMA
    # critica representativa.
    if c5_vend1:
        e1_vend_parc1 = _safe_get(parc1, "E1_VEND1") if parc1 else ""
        e1_vend_first = e1_vend_parc1 or (_safe_get(parcelas[0], "E1_VEND1") if parcelas else "")
        if e1_vend_first and e1_vend_first != c5_vend1:
            out.append({
                "field": "com_fraude_vendedor",
                "label": "Vendedor do Título (Comissionamento)",
                "category": "commercial",
                "item_n": None,
                "protheus_value": f"SC5.C5_VEND1 = {c5_vend1}",
                "xml_value": f"SE1.E1_VEND1 = {e1_vend_first}",
                "status": "divergent",
                "severity": "critical",
                "note": "Vendedor no titulo divergente do pedido — risco de comissao paga para vendedor errado",
            })

    # ---- R2: Prazo (WARN) ----
    # C5_DATA1 vs E1_VENCREA (preferencia) ou E1_VENCTO da parcela 1.
    if c5_data1 and parc1:
        e1_venc_real = _safe_get(parc1, "E1_VENCREA") or _safe_get(parc1, "E1_VENCTO")
        if e1_venc_real and e1_venc_real != c5_data1:
            out.append({
                "field": "com_prazo_pagamento",
                "label": "Prazo de Pagamento (Vencimento parcela 1)",
                "category": "commercial",
                "item_n": None,
                "protheus_value": f"SC5.C5_DATA1 = {c5_data1}",
                "xml_value": f"SE1.E1_VENCREA/VENCTO = {e1_venc_real}",
                "status": "divergent",
                "severity": "warn",
                "note": "Vencimento real do titulo diverge do prazo negociado no pedido — inadimplencia mascarada?",
            })

    # ---- R3: Desconto (WARN) ----
    # Compara C5_DESCFI/DESC1 com E1_DESCONT da parcela 1 (tol R$ 0,05).
    if c5_desc is not None and parc1:
        e1_desc = _to_decimal(parc1.get("E1_DESCONT")) or Decimal("0.00")
        if not (c5_desc == 0 and e1_desc == 0):
            tol = comparators.tol_valor()
            diff = abs(c5_desc - e1_desc)
            if diff > tol:
                out.append({
                    "field": "com_desconto_financeiro",
                    "label": "Desconto Financeiro",
                    "category": "commercial",
                    "item_n": None,
                    "protheus_value": f"SC5.C5_DESCFI/DESC1 = R$ {c5_desc}",
                    "xml_value": f"SE1.E1_DESCONT (parc.1) = R$ {e1_desc}",
                    "status": "divergent",
                    "severity": "warn",
                    "note": f"Desconto financeiro diverge em R$ {diff:.2f} (tolerancia R$ {tol})",
                })

    return out


def run_commercial_audit_for_branch(
    db_engine, branch: str, date_from: date, date_to: date,
) -> tuple[list[dict], list[dict]]:
    """Pipeline pronto para o orquestrador.

    Retorna (docs_processados, anomalias) — cada anomalia ja no formato
    persistivel em FiscalAnomaly.
    """
    docs = _load_commercial_audit_period(db_engine, branch, date_from, date_to)
    anomalies: list[dict] = []
    for doc in docs:
        divs = audit_commercial_doc(doc)
        for d in divs:
            anomalies.append({
                "doc_key": doc["doc_key"],
                "branch":  branch,
                "field":   d["field"],
                "label":   d["label"],
                "protheus_value": d["protheus_value"],
                "xml_value":      d["xml_value"],
                "severity":       d["severity"],
                "note":           d["note"],
            })
    return docs, anomalies
