"""Finance Audit Engine — Sprint 20 (Guardião do Caixa).

Cruzamento Contas a Pagar:
  SF1 (NF de entrada / cabecalho fiscal) ⚔ SE2 (Titulos do Contas a Pagar)

Condicao de JOIN (definida pelo cliente):
  SF1.F1_DOC = SE2.E2_NUM
  AND SF1.F1_FORNECE = SE2.E2_FORNECE
  AND SF1.F1_LOJA = SE2.E2_LOJA

Regras aplicadas:
  R0  - Ausencia de Titulo (CRITICAL): SF1 existe, SE2 inexistente
        (excluindo notas de remessa via TES, se possivel)
  R1  - Valor do Titulo  (CRITICAL): SF1.F1_VALBRUT vs SOMA(SE2.E2_VALOR) (tol R$ 0,05)
  R2  - Data Emissao     (WARN)    : SF1.F1_EMISSAO vs SE2.E2_EMISSAO
  R3  - IRRF retido      (WARN)    : SF1.F1_VALIR  vs SE2.E2_VALIR
  R4  - ISS retido       (WARN)    : SF1.F1_VALISS vs SE2.E2_VALISS

Quando a nota foi RATEADA em multiplas parcelas (E2_PARCELA), agregamos
SE2 por (E2_NUM, E2_FORNECE, E2_LOJA) somando E2_VALOR/IRRF/ISS antes de
comparar — a tolerancia (R$ 0,05) absorve arredondamento de rateio.

Saida: cada anomalia segue o schema padrao do `FiscalAnomaly`:
    {field, label, protheus_value, xml_value, status, severity, note,
     category="finance", item_n=None}
`xml_value` aqui guarda o lado SE2 (titulo); `protheus_value` guarda SF1
para manter compatibilidade com a UI existente (Protheus = lado ERP cadastrado).
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy import bindparam, text

from ..security import settings_store
from .internal_audit import (
    _is_missing_table_or_col,
    _resolve_branch,
    _safe_get,
    _safe_str_list,
    _t,
)
from . import comparators

logger = logging.getLogger(__name__)


# ============================================================
#  Colunas oficiais SE2 (Contas a Pagar) — confirmar com cliente
#  se alguma release usa nome diferente.
# ============================================================

SE2_COLS = (
    "E2_FILIAL, E2_PREFIXO, E2_NUM, E2_PARCELA, E2_TIPO, "
    "E2_FORNECE, E2_LOJA, E2_EMISSAO, E2_VENCREA, "
    "E2_VALOR, E2_SALDO, E2_VALIR, E2_VALISS, E2_BAIXA"
)

# Colunas SF1 que o motor precisa (alem das ja carregadas no internal_audit).
# Reusamos o conjunto principal SF1 + adicionamos os de impostos retidos.
SF1_COLS = (
    "F1_FILIAL, F1_DOC, F1_SERIE, F1_FORNECE, F1_LOJA, "
    "F1_EMISSAO, F1_TIPO, F1_VALBRUT, F1_VALIR, F1_VALISS"
)


# ============================================================
#  Loader: SF1 LEFT JOIN SE2 (com agregacao por parcela)
# ============================================================

def _load_finance_audit_period(
    db_engine,
    branch: str,
    date_from: date,
    date_to: date,
) -> list[dict]:
    """Carrega o periodo do MOTOR FINANCEIRO em batch.

    Estrategia:
    1. SELECT SF1 do periodo (filtra F1_EMISSAO BETWEEN df AND dt).
    2. LEFT JOIN SE2 por (F1_DOC=E2_NUM, F1_FORNECE=E2_FORNECE, F1_LOJA=E2_LOJA).
    3. Agrega em Python por (NUM, FORNECE, LOJA): soma E2_VALOR/VALIR/VALISS,
       guarda 1 E2_EMISSAO representativo (min) para a comparacao de data.

    Retorna lista de dicts:
        {
          "doc_key": "F1_DOC|F1_SERIE|F1_FORNECE|F1_LOJA",
          "branch":  "01",
          "sf1":     {F1_*: valor, ...},
          "se2":     {E2_TOTAL_VALOR, E2_TOTAL_VALIR, E2_TOTAL_VALISS,
                      E2_EMISSAO_MIN, parcelas: [...]} | None,
        }
    """
    suffix = settings_store.get_setting("PROTHEUS_TABLE_SUFFIX", "0")
    sf1_tbl = _t("SF1", branch, suffix)
    se2_tbl = _t("SE2", branch, suffix)
    _, filial_filter = _resolve_branch(branch)

    df = date_from.strftime("%Y%m%d")
    dt = date_to.strftime("%Y%m%d")

    sf1_cols_q = ", ".join(f"sf1.{c.strip()}" for c in SF1_COLS.split(","))
    se2_cols_q = ", ".join(f"se2.{c.strip()}" for c in SE2_COLS.split(","))

    # LEFT JOIN — SE2 pode nao existir (Ausencia de Titulo)
    filial_clause = ""
    if filial_filter:
        filial_clause = " AND sf1.F1_FILIAL = :filial"

    sql = (
        f"SELECT {sf1_cols_q}, {se2_cols_q} "
        f"FROM {sf1_tbl} sf1 WITH (NOLOCK) "
        f"LEFT JOIN {se2_tbl} se2 WITH (NOLOCK) "
        f"  ON se2.D_E_L_E_T_ = ' ' "
        f"  AND se2.E2_NUM = sf1.F1_DOC "
        f"  AND se2.E2_FORNECE = sf1.F1_FORNECE "
        f"  AND se2.E2_LOJA = sf1.F1_LOJA "
        f"WHERE sf1.D_E_L_E_T_ = ' ' "
        f"  AND sf1.F1_EMISSAO BETWEEN :df AND :dt" + filial_clause + " "
        f"ORDER BY sf1.F1_EMISSAO, sf1.F1_DOC, se2.E2_PARCELA"
    )
    params: dict = {"df": df, "dt": dt}
    if filial_filter:
        params["filial"] = filial_filter

    sf1_keys = [c.strip() for c in SF1_COLS.split(",")]
    se2_keys = [c.strip() for c in SE2_COLS.split(",")]

    aggregated: dict[tuple, dict] = {}   # (F1_DOC, F1_SERIE, F1_FORNECE, F1_LOJA) -> doc
    try:
        with db_engine.connect() as conn:
            rows = conn.execute(text(sql), params).all()
    except Exception as exc:
        if _is_missing_table_or_col(exc):
            logger.warning(
                "SE2/SF1 (%s/%s) — colunas inexistentes nesta release: %s",
                se2_tbl, sf1_tbl, exc,
            )
            return []
        raise

    for r in rows:
        m = dict(r._mapping)
        sf1 = {k: m.get(k) for k in sf1_keys}
        key = (
            _safe_get(sf1, "F1_DOC"),
            _safe_get(sf1, "F1_SERIE"),
            _safe_get(sf1, "F1_FORNECE"),
            _safe_get(sf1, "F1_LOJA"),
        )
        if not key[0]:
            continue

        doc = aggregated.get(key)
        if doc is None:
            doc = {
                "doc_key": "|".join(key),
                "branch": branch,
                "sf1": sf1,
                "se2": None,
            }
            aggregated[key] = doc

        # Tem parcela de SE2?
        e2_num = _safe_get(m, "E2_NUM")
        if not e2_num:
            continue  # LEFT JOIN sem match — Ausencia de Titulo

        se2_payload = doc["se2"]
        if se2_payload is None:
            se2_payload = {
                "E2_TOTAL_VALOR": Decimal("0.00"),
                "E2_TOTAL_VALIR": Decimal("0.00"),
                "E2_TOTAL_VALISS": Decimal("0.00"),
                "E2_EMISSAO_MIN": None,
                "parcelas": [],
            }
            doc["se2"] = se2_payload

        # Soma valores (tolerante a None / formato invalido)
        def _add_dec(field, key_target):
            raw = m.get(field)
            try:
                if raw is not None and str(raw).strip() != "":
                    se2_payload[key_target] += Decimal(str(raw).strip().replace(",", "."))
            except (InvalidOperation, ValueError, TypeError):
                pass
        _add_dec("E2_VALOR", "E2_TOTAL_VALOR")
        _add_dec("E2_VALIR", "E2_TOTAL_VALIR")
        _add_dec("E2_VALISS", "E2_TOTAL_VALISS")

        e2_emi = _safe_get(m, "E2_EMISSAO")
        if e2_emi and (se2_payload["E2_EMISSAO_MIN"] is None
                       or e2_emi < se2_payload["E2_EMISSAO_MIN"]):
            se2_payload["E2_EMISSAO_MIN"] = e2_emi

        se2_payload["parcelas"].append({
            "E2_PARCELA": _safe_get(m, "E2_PARCELA"),
            "E2_TIPO":    _safe_get(m, "E2_TIPO"),
            "E2_EMISSAO": e2_emi,
            "E2_VENCREA": _safe_get(m, "E2_VENCREA"),
            "E2_VALOR":   _safe_get(m, "E2_VALOR"),
            "E2_BAIXA":   _safe_get(m, "E2_BAIXA"),
        })

    docs = list(aggregated.values())
    sem_titulo = sum(1 for d in docs if not d["se2"])
    logger.info(
        "FinanceAudit %s: %d SF1, %d sem SE2 (Ausencia de Titulo)",
        branch, len(docs), sem_titulo,
    )
    return docs


# ============================================================
#  Engine de regras — produz a lista de divergencias
# ============================================================

# TES de "remessa" / "simples faturamento" que NAO geram titulo (skip R0).
# Configuravel via AppSetting `FINANCE_SKIP_TIPOS` (lista CSV).
# Defaults conservadores — operador pode customizar.
_DEFAULT_SKIP_TIPOS = {"RE", "DV", "CT", "BO"}   # remessa, devolucao, complemento, bonificacao


def _skip_tipos() -> set[str]:
    raw = settings_store.get_setting("FINANCE_SKIP_TIPOS", "")
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


def audit_finance_doc(doc: dict) -> list[dict]:
    """Aplica as 5 regras de financeiro em um doc agregado e devolve a lista
    de itens (apenas divergencias — para colocar em FiscalAnomaly).
    """
    out: list[dict] = []
    sf1 = doc.get("sf1") or {}
    se2 = doc.get("se2")
    f1_doc = _safe_get(sf1, "F1_DOC")
    f1_tipo = _safe_get(sf1, "F1_TIPO").upper()

    # R0 — Ausencia de Titulo
    if se2 is None:
        if f1_tipo and f1_tipo in _skip_tipos():
            # Esperado: remessa/devolucao nao gera titulo
            return out
        out.append({
            "field": "fin_ausencia_titulo",
            "label": "Ausência de Título",
            "category": "finance",
            "item_n": None,
            "protheus_value": f"NF {f1_doc} (TIPO={f1_tipo or '?'})",
            "xml_value": "(sem registro em SE2)",
            "status": "divergent",
            "severity": "critical",
            "note": "Nota Fiscal SF1 sem titulo correspondente no Contas a Pagar (SE2)",
        })
        return out

    # R1 — Valor do Titulo (CRITICAL, tol R$ 0,05)
    valbrut = _to_decimal(sf1.get("F1_VALBRUT"))
    soma_titulo = se2.get("E2_TOTAL_VALOR") or Decimal("0.00")
    if valbrut is not None and soma_titulo is not None:
        tol = comparators.tol_valor()
        diff = abs(valbrut - soma_titulo.quantize(Decimal("0.01")))
        if diff > tol:
            out.append({
                "field": "fin_valor_titulo",
                "label": "Valor do Título",
                "category": "finance",
                "item_n": None,
                "protheus_value": f"SF1.F1_VALBRUT = R$ {valbrut}",
                "xml_value": f"SOMA(SE2.E2_VALOR) = R$ {soma_titulo} "
                             f"({len(se2.get('parcelas', []))} parcela(s))",
                "status": "divergent",
                "severity": "critical",
                "note": f"Valor da NF diverge do total do titulo em R$ {diff:.2f} "
                        f"(tolerancia R$ {tol})",
            })

    # R2 — Data Emissao (WARN) — usa E2_EMISSAO_MIN (1a parcela emitida)
    f1_emi = _safe_get(sf1, "F1_EMISSAO")
    e2_emi = (se2.get("E2_EMISSAO_MIN") or "")
    if f1_emi and e2_emi and f1_emi != e2_emi:
        out.append({
            "field": "fin_data_emissao",
            "label": "Data de Emissão",
            "category": "finance",
            "item_n": None,
            "protheus_value": f"SF1.F1_EMISSAO = {f1_emi}",
            "xml_value": f"SE2.E2_EMISSAO = {e2_emi}",
            "status": "divergent",
            "severity": "warn",
            "note": "Data de emissao difere entre a NF (SF1) e o titulo (SE2)",
        })

    # R3 — IRRF retido (WARN)
    f1_ir = _to_decimal(sf1.get("F1_VALIR"))
    e2_ir = se2.get("E2_TOTAL_VALIR")
    if f1_ir is not None and e2_ir is not None:
        if not (f1_ir == 0 and e2_ir == 0):
            tol = comparators.tol_icms()
            diff = abs(f1_ir - e2_ir.quantize(Decimal("0.01")))
            if diff > tol:
                out.append({
                    "field": "fin_irrf",
                    "label": "IRRF Retido",
                    "category": "finance",
                    "item_n": None,
                    "protheus_value": f"SF1.F1_VALIR = R$ {f1_ir}",
                    "xml_value": f"SOMA(SE2.E2_VALIR) = R$ {e2_ir}",
                    "status": "divergent",
                    "severity": "warn",
                    "note": f"IRRF diverge em R$ {diff:.2f}",
                })

    # R4 — ISS retido (WARN)
    f1_iss = _to_decimal(sf1.get("F1_VALISS"))
    e2_iss = se2.get("E2_TOTAL_VALISS")
    if f1_iss is not None and e2_iss is not None:
        if not (f1_iss == 0 and e2_iss == 0):
            tol = comparators.tol_icms()
            diff = abs(f1_iss - e2_iss.quantize(Decimal("0.01")))
            if diff > tol:
                out.append({
                    "field": "fin_iss",
                    "label": "ISS Retido",
                    "category": "finance",
                    "item_n": None,
                    "protheus_value": f"SF1.F1_VALISS = R$ {f1_iss}",
                    "xml_value": f"SOMA(SE2.E2_VALISS) = R$ {e2_iss}",
                    "status": "divergent",
                    "severity": "warn",
                    "note": f"ISS retido diverge em R$ {diff:.2f}",
                })

    return out


def run_finance_audit_for_branch(
    db_engine, branch: str, date_from: date, date_to: date,
) -> tuple[list[dict], list[dict]]:
    """Roda o motor financeiro para uma filial inteira no periodo.

    Retorna (docs_processados, anomalias_geradas) onde:
      - docs_processados: lista plana de docs (com sf1 + se2 agregada).
      - anomalias_geradas: cada item ja no formato persistivel em FiscalAnomaly,
        com `doc_key`, `branch`, `field`, `severity`, etc.
    """
    docs = _load_finance_audit_period(db_engine, branch, date_from, date_to)
    anomalies: list[dict] = []
    for doc in docs:
        divs = audit_finance_doc(doc)
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
