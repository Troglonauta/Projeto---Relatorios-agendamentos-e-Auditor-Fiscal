"""Internal Audit Engine — Sprint 13 (Relatorio Completo + novo mapeamento).

Auditoria 100% interna ao Protheus:

  SDS (XML internalizado) ⚔ SF1 (Nota classificada)
  SDT (Itens do XML)      ⚔ SD1 (Itens da nota)
  SA2 (Cadastro fornecedor) — para resolver CNPJ via F1_FORNECE+F1_LOJA

Sprint 13 — colunas oficiais expandidas + variantes SDT detectadas em runtime
(o cliente confirma que algumas releases tem DT_XBASICM/DT_XMLICM/DT_ALIQICM/
DT_CODCFOP/DT_DESCFOR, outras tem DT_BASEICM/DT_VALICM/DT_PICM/DT_CODCFEN/DT_DESC).

Estrutura do "doc" produzido por `_load_audit_period_internal`:

    {
      "chave":  "44 digitos (ou vazio se SDS nao tinha)",
      "branch": "01",
      "sds":    { DS_*: valor, ... },
      "sf1":    { F1_*: valor, ... } | None,   # None => "Nota Ausente"
      "sa2":    { A2_CGC: ..., A2_NOME: ... } | None,
      "sdt_items": [ {DT_*: valor_normalizado, ...}, ... ],  # com aliases canonicos
      "sd1_items": [ {D1_*: valor, ...}, ... ],
    }

SDT vem com colunas RENOMEADAS para nomes canonicos (DT_BASEICM, DT_VALICM,
DT_PICM, DT_CFOP, DT_DESC) via SQL alias — o rule_engine sempre le esses nomes
mesmo se o cliente tem schema DT_XBASICM/etc.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from sqlalchemy import bindparam, text

from ..security import settings_store

logger = logging.getLogger(__name__)


# ============================================================
#  Colunas OFICIAIS Fertimaxi (Sprint 13 — 2026-05-27)
# ============================================================

SDS_COLS = (
    "DS_FILIAL, DS_DOC, DS_SERIE, DS_FORNEC, DS_LOJA, "
    "DS_CNPJ, DS_EMISSA, DS_CHAVENF, "
    "DS_VALMERC, DS_TOTAL, DS_BASEICM, DS_VALICM, "
    "DS_FRETE, DS_SEGURO, DS_DESCONT, DS_DESPESA"
)
SF1_COLS = (
    "F1_FILIAL, F1_DOC, F1_SERIE, F1_FORNECE, F1_LOJA, "
    "F1_EMISSAO, F1_DTDIGIT, F1_ESPECIE, F1_CHVNFE, "
    "F1_VALMERC, F1_VALBRUT, F1_BASEICM, F1_VALICM, "
    "F1_FRETE, F1_SEGURO, F1_DESCONT, F1_DESPESA"
)
SD1_COLS = (
    "D1_FILIAL, D1_DOC, D1_SERIE, D1_FORNECE, D1_LOJA, D1_ITEM, "
    "D1_COD, D1_QUANT, D1_VUNIT, D1_TOTAL, "
    "D1_PICM, D1_VALICM, D1_BASEICM, D1_CLASFIS, D1_DESC, "
    "D1_CF, D1_TEC"
)
SA2_COLS = "A2_FILIAL, A2_COD, A2_LOJA, A2_CGC, A2_NOME, A2_EST"

# SDT — colunas CANONICAS que o rule_engine consome.
# Mapeamos em runtime para o nome real via _detect_sdt_columns().
SDT_CANONICAL = (
    "DT_FILIAL", "DT_DOC", "DT_SERIE", "DT_FORNEC", "DT_LOJA", "DT_ITEM",
    "DT_COD", "DT_DESC", "DT_QUANT", "DT_VUNIT", "DT_TOTAL",
    "DT_BASEICM", "DT_VALICM", "DT_PICM", "DT_CFOP", "DT_CLASFIS",
)


def _resolve_branch(branch: str) -> tuple[str, str | None]:
    """Sprint 20 Bug 1 — Resolve a filial em DUAS partes:
      - `table_branch` (str de 2 chars): usado para montar o nome FÍSICO da tabela
      - `filial_filter` (str de 4 chars ou None): usado no WHERE *_FILIAL = :filial

    Cenarios suportados:
      - branch="01" (2 dig)   -> ("01", None)        — comportamento legado
      - branch="0101" (4 dig) -> ("01", "0101")      — empresa+filial, tabela e' SDS010
      - branch="01010" etc   -> ("01", branch)       — fallback defensivo

    Em ambientes multi-empresa do Protheus, a tabela e' criada pelos 2 PRIMEIROS
    digitos (empresa) + sufixo, mas o registro tem FILIAL preenchido com os 4 digitos
    completos. Sem essa separacao o `_t()` tentava buscar SDS01010 (inexistente).
    """
    b = (branch or "").strip()
    if len(b) >= 4:
        return b[:2], b
    return b, None


def _t(alias: str, branch: str, suffix: str) -> str:
    """Nome FÍSICO da tabela Protheus. Para filiais de 4 digitos, usa so os
    2 primeiros para localizar a tabela (ver `_resolve_branch`)."""
    table_branch, _ = _resolve_branch(branch)
    return f"{alias}{table_branch}{suffix}"


def _is_missing_table_or_col(exc: Exception) -> bool:
    try:
        import pyodbc
        NOISY = (pyodbc.ProgrammingError,)
    except Exception:
        NOISY = ()
    if NOISY and isinstance(exc, NOISY):
        return True
    orig = getattr(exc, "orig", None)
    if orig is not None and NOISY and isinstance(orig, NOISY):
        return True
    return type(exc).__name__ == "ProgrammingError"


def _safe_str_list(values, max_items: int = 10000) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if not v:
            continue
        s = str(v).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= max_items:
            break
    return out


def _safe_get(d: Optional[dict], key: str, default: str = "") -> str:
    if not d:
        return default
    v = d.get(key)
    if v is None:
        return default
    return str(v).strip()


# ============================================================
#  Deteccao dinamica de colunas SDT (DT_XBASICM vs DT_BASEICM, etc.)
# ============================================================

_SDT_COL_CACHE: dict[str, dict[str, str]] = {}


def _detect_sdt_columns(conn, table: str) -> dict[str, str]:
    """Descobre quais variantes de coluna existem em SDT e devolve
    mapping {canonico -> real}. Cacheia por nome de tabela."""
    if table in _SDT_COL_CACHE:
        return _SDT_COL_CACHE[table]

    real_cols: set[str] = set()
    try:
        bare = table.split(".")[-1]
        rows = conn.execute(
            text(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME = :t"
            ),
            {"t": bare},
        ).all()
        real_cols = {(r[0] or "").upper() for r in rows}
    except Exception as exc:
        logger.warning("INFORMATION_SCHEMA falhou para %s: %s", table, exc)

    def pick(*candidates: str) -> Optional[str]:
        for c in candidates:
            if c.upper() in real_cols:
                return c
        return None

    mapping: dict[str, str] = {}
    # Identificacao
    for k in ("DT_FILIAL", "DT_DOC", "DT_SERIE", "DT_FORNEC", "DT_LOJA",
              "DT_ITEM", "DT_COD", "DT_QUANT", "DT_VUNIT", "DT_TOTAL",
              "DT_CLASFIS"):
        r = pick(k)
        if r:
            mapping[k] = r
    # Variantes ICMS — base/valor/aliquota. Prioriza os campos do cliente
    # (DT_XBASICM / DT_XMLICM / DT_XALQICM), com fallback p/ nomes padrao.
    bicm = pick("DT_XBASICM", "DT_BASEICM")
    if bicm:
        mapping["DT_BASEICM"] = bicm
    vicm = pick("DT_XMLICM", "DT_VALICM", "DT_VICMS")
    if vicm:
        mapping["DT_VALICM"] = vicm
    picm = pick("DT_XALQICM", "DT_ALIQICM", "DT_PICM")
    if picm:
        mapping["DT_PICM"] = picm
    # CFOP
    cfop = pick("DT_CODCFOP", "DT_CODCFEN", "DT_CFOP")
    if cfop:
        mapping["DT_CFOP"] = cfop
    # Descricao
    desc = pick("DT_DESCFOR", "DT_DESC")
    if desc:
        mapping["DT_DESC"] = desc

    _SDT_COL_CACHE[table] = mapping
    logger.info(
        "SDT %s — colunas detectadas: %s",
        table, {k: v for k, v in mapping.items() if k != v},
    )
    return mapping


_COL_EXISTS_CACHE: dict[tuple, bool] = {}


def _table_has_column(conn, table: str, column: str) -> bool:
    """Checa (com cache) se uma coluna existe — permite incluir colunas
    OPCIONAIS (ex: DS_SDOC, serie do CTe) sem quebrar releases que nao a tem."""
    key = (table, column.upper())
    if key in _COL_EXISTS_CACHE:
        return _COL_EXISTS_CACHE[key]
    exists = False
    try:
        bare = table.split(".")[-1]
        row = conn.execute(
            text(
                "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME = :t AND COLUMN_NAME = :c"
            ),
            {"t": bare, "c": column.upper()},
        ).first()
        exists = row is not None
    except Exception as exc:
        logger.warning("Checagem de coluna %s.%s falhou: %s", table, column, exc)
    _COL_EXISTS_CACHE[key] = exists
    return exists


# ============================================================
#  Loader principal
# ============================================================

def _load_audit_period_internal(
    db_engine,
    branch: str,
    date_from: date,
    date_to: date,
    *,
    chave_filter: Optional[str] = None,
    doc_filter: Optional[dict] = None,
) -> list[dict]:
    """Carrega o periodo da AUDITORIA INTERNA em batch.

    Sprint 13 — diferencas vs Sprint 12:
    - SA2 carregado para resolver CNPJ via F1_FORNECE+F1_LOJA.
    - SDT lido com SELECT aliasing para neutralizar variantes
      DT_XBASICM/DT_BASEICM, DT_XMLICM/DT_VALICM, DT_ALIQICM/DT_PICM,
      DT_CODCFOP/DT_CODCFEN, DT_DESCFOR/DT_DESC.
    - Colunas novas em SDS/SF1: VALMERC, FRETE, SEGURO, DESCONT, DESPESA.

    Se `chave_filter` for passado, busca apenas essa chave (sem periodo).
    """
    suffix = settings_store.get_setting("PROTHEUS_TABLE_SUFFIX", "0")
    sds_tbl = _t("SDS", branch, suffix)
    sdt_tbl = _t("SDT", branch, suffix)
    sf1_tbl = _t("SF1", branch, suffix)
    sd1_tbl = _t("SD1", branch, suffix)
    sa2_tbl = _t("SA2", branch, suffix)
    # Sprint 20 Bug 1 — para filial de 4 dig (ex: 0101), `_t` busca tabela SDS010
    # mas o WHERE precisa filtrar pelo valor COMPLETO (DS_FILIAL = '0101').
    _, filial_filter = _resolve_branch(branch)

    df = date_from.strftime("%Y%m%d")
    dt = date_to.strftime("%Y%m%d")

    docs: list[dict] = []

    with db_engine.connect() as conn:
        # ---- 1) SDS + LEFT JOIN SF1 com MATCH TRIPLO ---------------------
        sds_col_list = [c.strip() for c in SDS_COLS.split(",")]
        # Colunas OPCIONAIS do cliente — incluidas so se existirem (defensivo):
        #   DS_SDOC    -> serie alternativa do CTe
        #   DS_ESPECI  -> especie do documento (cruza com F1_ESPECIE)
        #   DS_DATAIMP -> data de importacao/digitacao (cruza com F1_DTDIGIT)
        for _opt in ("DS_SDOC", "DS_ESPECI", "DS_DATAIMP"):
            if _table_has_column(conn, sds_tbl, _opt):
                sds_col_list.append(_opt)
        sds_cols_q = ", ".join(f"sds.{c}" for c in sds_col_list)
        sf1_cols_q = ", ".join(f"sf1.{c.strip()}" for c in SF1_COLS.split(","))
        join_match = (
            "(sf1.F1_CHVNFE = sds.DS_CHAVENF AND sf1.F1_CHVNFE <> ' ') "
            "OR ("
            "  sf1.F1_DOC = sds.DS_DOC "
            "  AND sf1.F1_SERIE = sds.DS_SERIE "
            "  AND sf1.F1_FORNECE = sds.DS_FORNEC "
            "  AND sf1.F1_LOJA = sds.DS_LOJA"
            ")"
        )
        # Sprint 20 — multi-empresa: clausula extra so quando ha filial_filter
        filial_clause = " AND sds.DS_FILIAL = :filial" if filial_filter else ""
        if chave_filter:
            where = "sds.D_E_L_E_T_ = ' ' AND sds.DS_CHAVENF = :chv" + filial_clause
            params: dict = {"chv": chave_filter}
        elif doc_filter:
            # Fix #4 — abre documento SEM chave de 44 digitos (NF avulsa/CTe sem
            # chave NFe) localizando por doc + serie + fornecedor + loja.
            where = (
                "sds.D_E_L_E_T_ = ' ' "
                "AND sds.DS_DOC = :doc AND sds.DS_SERIE = :serie "
                "AND sds.DS_FORNEC = :fornec AND sds.DS_LOJA = :loja" + filial_clause
            )
            params = {
                "doc":    doc_filter.get("doc", ""),
                "serie":  doc_filter.get("serie", ""),
                "fornec": doc_filter.get("fornec", ""),
                "loja":   doc_filter.get("loja", ""),
            }
        else:
            where = (
                "sds.D_E_L_E_T_ = ' ' "
                "AND sds.DS_EMISSA BETWEEN :df AND :dt" + filial_clause
            )
            params = {"df": df, "dt": dt}
        if filial_filter:
            params["filial"] = filial_filter

        sql = (
            f"SELECT {sds_cols_q}, {sf1_cols_q} "
            f"FROM {sds_tbl} sds WITH (NOLOCK) "
            f"LEFT JOIN {sf1_tbl} sf1 WITH (NOLOCK) "
            f"  ON sf1.D_E_L_E_T_ = ' ' AND ({join_match}) "
            f"WHERE {where} "
            f"ORDER BY sds.DS_EMISSA, sds.DS_DOC"
        )
        try:
            rows = conn.execute(text(sql), params).all()
        except Exception as exc:
            if _is_missing_table_or_col(exc):
                logger.warning(
                    "Tabela %s/%s ou colunas inexistentes nesta release: %s",
                    sds_tbl, sf1_tbl, exc,
                )
                return []
            raise

        sds_keys = sds_col_list
        sf1_keys = [c.strip() for c in SF1_COLS.split(",")]

        sds_docs: set[str] = set()
        sf1_docs: set[str] = set()
        sa2_keys: set[tuple] = set()    # (A2_COD, A2_LOJA)

        for r in rows:
            m = dict(r._mapping)
            sds_dict = {k: m.get(k) for k in sds_keys}
            chave = _safe_get(sds_dict, "DS_CHAVENF")
            sf1_dict = {k: m.get(k) for k in sf1_keys}
            has_sf1 = bool(_safe_get(sf1_dict, "F1_DOC"))
            sf1_payload = sf1_dict if has_sf1 else None

            docs.append({
                "chave": chave,
                "branch": branch,
                "sds": sds_dict,
                "sf1": sf1_payload,
                "sa2": None,
                "sdt_items": [],
                "sd1_items": [],
            })

            ds_doc = _safe_get(sds_dict, "DS_DOC")
            if ds_doc:
                sds_docs.add(ds_doc)

            if has_sf1:
                f1_doc = _safe_get(sf1_dict, "F1_DOC")
                if f1_doc:
                    sf1_docs.add(f1_doc)
                a2_key = (
                    _safe_get(sf1_dict, "F1_FORNECE"),
                    _safe_get(sf1_dict, "F1_LOJA"),
                )
                if a2_key[0]:
                    sa2_keys.add(a2_key)

        if not docs:
            return []

        # ---- 2) SDT com SELECT aliasing (variantes DT_*) --------------------
        sdt_by_key: dict[tuple, list[dict]] = {}
        if sds_docs:
            col_map = _detect_sdt_columns(conn, sdt_tbl)
            if not col_map:
                logger.warning(
                    "SDT %s: nenhuma coluna detectada — auditoria de itens vazia",
                    sdt_tbl,
                )
            else:
                # Monta SELECT real_col AS canonico
                select_parts = []
                for canon in SDT_CANONICAL:
                    real = col_map.get(canon)
                    if real:
                        select_parts.append(
                            f"{real} AS {canon}" if real != canon else canon
                        )
                # Coluna chave para o IN é DT_DOC (canonico bate com real)
                doc_col = col_map.get("DT_DOC", "DT_DOC")
                item_col = col_map.get("DT_ITEM", "DT_ITEM")
                try:
                    # Sprint 20 — multi-empresa
                    filial_col = col_map.get("DT_FILIAL", "DT_FILIAL")
                    extra = f" AND {filial_col} = :filial" if filial_filter else ""
                    stmt = text(
                        f"SELECT {', '.join(select_parts)} "
                        f"FROM {sdt_tbl} WITH (NOLOCK) "
                        f"WHERE D_E_L_E_T_ = ' ' AND {doc_col} IN :vals{extra} "
                        f"ORDER BY {doc_col}, {item_col}"
                    ).bindparams(bindparam("vals", expanding=True))
                    bind_params = {"vals": _safe_str_list(sds_docs)}
                    if filial_filter:
                        bind_params["filial"] = filial_filter
                    for r in conn.execute(stmt, bind_params).all():
                        row = dict(r._mapping)
                        key = (
                            _safe_get(row, "DT_DOC"),
                            _safe_get(row, "DT_SERIE"),
                            _safe_get(row, "DT_FORNEC"),
                            _safe_get(row, "DT_LOJA"),
                        )
                        if key[0]:
                            sdt_by_key.setdefault(key, []).append(row)
                except Exception as exc:
                    if _is_missing_table_or_col(exc):
                        logger.debug("SDT %s coluna ausente: %s", sdt_tbl, exc)
                    else:
                        logger.warning("SDT load falhou: %s", exc)

        # ---- 3) SD1 (itens nota classificada) — batch por D1_DOC ------------
        sd1_by_key: dict[tuple, list[dict]] = {}
        if sf1_docs:
            try:
                # Sprint 20 — multi-empresa
                extra = " AND D1_FILIAL = :filial" if filial_filter else ""
                # D1_FSDPROD — descricao do produto (campo do cliente). Incluido
                # so se existir; o cruzamento de descricao usa D1_FSDPROD x DT_DESCFOR.
                sd1_cols_sql = SD1_COLS
                if _table_has_column(conn, sd1_tbl, "D1_FSDPROD"):
                    sd1_cols_sql = SD1_COLS + ", D1_FSDPROD"
                stmt = text(
                    f"SELECT {sd1_cols_sql} FROM {sd1_tbl} WITH (NOLOCK) "
                    f"WHERE D_E_L_E_T_ = ' ' AND D1_DOC IN :vals{extra} "
                    f"ORDER BY D1_DOC, D1_ITEM"
                ).bindparams(bindparam("vals", expanding=True))
                bind_params = {"vals": _safe_str_list(sf1_docs)}
                if filial_filter:
                    bind_params["filial"] = filial_filter
                for r in conn.execute(stmt, bind_params).all():
                    row = dict(r._mapping)
                    key = (
                        _safe_get(row, "D1_DOC"),
                        _safe_get(row, "D1_SERIE"),
                        _safe_get(row, "D1_FORNECE"),
                        _safe_get(row, "D1_LOJA"),
                    )
                    if key[0]:
                        sd1_by_key.setdefault(key, []).append(row)
            except Exception as exc:
                if _is_missing_table_or_col(exc):
                    logger.debug("SD1 %s coluna ausente: %s", sd1_tbl, exc)
                else:
                    logger.warning("SD1 load falhou: %s", exc)

        # ---- 4) SA2 (cadastro fornecedor) — para CNPJ -----------------------
        sa2_by_key: dict[tuple, dict] = {}
        if sa2_keys:
            try:
                stmt = text(
                    f"SELECT {SA2_COLS} FROM {sa2_tbl} WITH (NOLOCK) "
                    f"WHERE D_E_L_E_T_ = ' ' AND A2_COD IN :vals"
                ).bindparams(bindparam("vals", expanding=True))
                forn_codes = _safe_str_list(k[0] for k in sa2_keys)
                for r in conn.execute(stmt, {"vals": forn_codes}).all():
                    row = dict(r._mapping)
                    key = (
                        _safe_get(row, "A2_COD"),
                        _safe_get(row, "A2_LOJA"),
                    )
                    if key[0]:
                        sa2_by_key[key] = row
            except Exception as exc:
                if _is_missing_table_or_col(exc):
                    logger.debug("SA2 %s coluna ausente: %s", sa2_tbl, exc)
                else:
                    logger.warning("SA2 load falhou: %s", exc)

        # ---- 5) Anexa itens + SA2 em cada doc -------------------------------
        for d in docs:
            sds = d["sds"]
            sds_key = (
                _safe_get(sds, "DS_DOC"),
                _safe_get(sds, "DS_SERIE"),
                _safe_get(sds, "DS_FORNEC"),
                _safe_get(sds, "DS_LOJA"),
            )
            d["sdt_items"] = sdt_by_key.get(sds_key, [])

            sf1 = d["sf1"]
            if sf1:
                sf1_key = (
                    _safe_get(sf1, "F1_DOC"),
                    _safe_get(sf1, "F1_SERIE"),
                    _safe_get(sf1, "F1_FORNECE"),
                    _safe_get(sf1, "F1_LOJA"),
                )
                d["sd1_items"] = sd1_by_key.get(sf1_key, [])
                a2_key = (
                    _safe_get(sf1, "F1_FORNECE"),
                    _safe_get(sf1, "F1_LOJA"),
                )
                d["sa2"] = sa2_by_key.get(a2_key)

    with_sf1 = sum(1 for d in docs if d["sf1"])
    without_sf1 = len(docs) - with_sf1
    pct = 100 * with_sf1 / max(1, len(docs))
    logger.info(
        "InternalAudit %s: %d docs (SDS), %d com SF1 (%.1f%%), %d sem (Nota Ausente)",
        branch, len(docs), with_sf1, pct, without_sf1,
    )
    return docs
