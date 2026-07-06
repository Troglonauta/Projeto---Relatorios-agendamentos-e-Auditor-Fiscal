"""Motor de cruzamento de tabelas (JOINs dinamicos) — Sprint 5.

A camada Protheus original (`protheus_api.query_table`) suporta apenas
uma tabela. Este modulo cobre cenarios com N tabelas juntadas em cascata,
preservando seguranca (sanitizacao de identificadores) e performance
(paginacao OFFSET/FETCH no SQL Server).

Filosofia:
- Identificadores (tabela, coluna, alias) sao SEMPRE validados via regex
  estrita ([A-Z][A-Z0-9_]*) antes de entrar na string SQL. Valores vao
  como bind parameters.
- Cada tabela ganha um **alias SQL curto** (`t1`, `t2`, `t3`...). Isso
  evita repetir `SC5010.C5_NUM` no SELECT/WHERE quando o nome fisico e' longo
  e permite alinhar com o alias logico Protheus (`SC5`) no payload.
- A coluna do SELECT pode vir como `"FOO"` (auto-prefixado para a base) ou
  `"ALIAS.FOO"` (qualificado, evita ambiguidade em D_E_L_E_T_, FILIAL, etc).
- `D_E_L_E_T_ = ' '` aplica-se a CADA tabela do FROM/JOIN (regra Protheus).
- Apenas INNER e LEFT JOIN sao aceitos. RIGHT e CROSS sao deliberadamente
  rejeitados — adicione com cuidado caso precise.

Exemplo de uso:
    builder = JoinQueryBuilder(
        base=("SC5", "01"),
        joins=[
            JoinSpec("SA1", "01", "INNER", [JoinCond("SC5", "C5_CLIENTE", "A1_COD")]),
            JoinSpec("SA3", "01", "LEFT",  [JoinCond("SC5", "C5_VEND1",   "A3_COD")]),
        ],
        columns=["SC5.C5_NUM", "SA1.A1_NOME", "SA3.A3_NOME"],
        filters={"SC5.C5_EMISSAO__gte": "20260101"},
    )
    sql, count_sql, params = builder.build(page=1, page_size=100)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

from .protheus_aliases import is_known_alias
from .protheus_api import _OP_SUFFIXES, ProtheusError, _safe_ident

logger = logging.getLogger(__name__)

# Regex auxiliar — coluna qualificada `<ALIAS>.<COLUNA>`
_QUALIFIED_COL = re.compile(r"^([A-Za-z][A-Za-z0-9]{1,3})\.([A-Za-z][A-Za-z0-9_]{0,59})$")
_VALID_JOIN_TYPES = {"INNER", "LEFT"}

# Sprint 5 polish — protecao do banco contra queries monstruosas.
# 5 cobre 99% dos casos reais (SC5+SA1+SA3+SD2+SF2 etc); acima disso a
# query vira incompreensivel e tende a explodir o plano de execucao.
# Configuravel via AppSetting `QUERY_MAX_JOINS` se precisar relaxar.
DEFAULT_MAX_JOINS = 5


@dataclass(frozen=True)
class JoinCond:
    """Uma condição do ON: `<left_alias>.<left_column> = <this_alias>.<right_column>`."""
    left_alias: str
    left_column: str
    right_column: str


@dataclass(frozen=True)
class JoinSpec:
    """Uma tabela adicional juntada. `branch` é o código de filial (2 chars)."""
    alias: str
    branch: str
    join_type: str           # "INNER" | "LEFT"
    on: list[JoinCond]


def _table_name(alias: str, branch: str, suffix: str = "0") -> str:
    """Monta nome físico Protheus aplicando truncamento da filial em 2 chars
    (consistente com `protheus_api.resolve_table_name`).
    """
    alias = _safe_ident(alias, "Alias")
    branch = (branch or "").strip()[:2]
    if not re.match(r"^[A-Za-z0-9]{1,2}$", branch):
        raise ProtheusError(f"Filial inválida: {branch!r}")
    return f"{alias}{branch}{suffix}".upper()


class JoinQueryBuilder:
    """Builder thread-local — uma instância por requisição.

    Constrói duas queries:
    - `data_sql`  : `SELECT ... ORDER BY ... OFFSET ... ROWS FETCH NEXT ...`
    - `count_sql` : `SELECT COUNT_BIG(1) FROM ... JOIN ... WHERE ...`
      (mesmo WHERE, sem paginação — para o `total` do response).
    """

    def __init__(
        self,
        base: tuple[str, str],
        joins: list[JoinSpec] | None = None,
        columns: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        table_suffix: str = "0",
    ):
        if not is_known_alias(base[0]):
            raise ProtheusError(f"Alias base desconhecido: {base[0]!r}")
        self.base_alias = _safe_ident(base[0], "Alias base")
        self.base_branch = base[1]
        self.joins: list[JoinSpec] = list(joins or [])
        self.columns: list[str] = list(columns or [])
        self.filters: dict[str, Any] = dict(filters or {})
        self.table_suffix = table_suffix

        # Validações estruturais
        self._validate()

        # Mapas alias_logico → (table_fisica, alias_sql)
        # t1 = base; t2, t3... = joins na ordem.
        self._aliases: dict[str, dict[str, str]] = {}
        self._aliases[self.base_alias] = {
            "table": _table_name(self.base_alias, self.base_branch, table_suffix),
            "sql":   "t1",
        }
        for i, j in enumerate(self.joins, start=2):
            a = _safe_ident(j.alias, "Alias JOIN")
            if a in self._aliases:
                raise ProtheusError(
                    f"Alias '{a}' aparece duas vezes — duplicar a mesma tabela "
                    f"no mesmo JOIN nao e' suportado. Renomeie se for proposital."
                )
            self._aliases[a] = {
                "table": _table_name(a, j.branch, table_suffix),
                "sql":   f"t{i}",
            }

    # ---- Validações --------------------------------------------------------

    def _validate(self) -> None:
        # Sprint 5 polish — limite de JOINs (anti-abuso)
        max_joins = DEFAULT_MAX_JOINS
        try:
            from .security import settings_store
            cfg = settings_store.get_setting("QUERY_MAX_JOINS")
            if cfg is not None:
                max_joins = max(1, min(int(cfg), 20))
        except Exception:
            pass
        if len(self.joins) > max_joins:
            raise ProtheusError(
                f"Limite de {max_joins} JOINs por consulta excedido "
                f"(recebido: {len(self.joins)}). Reduza o cruzamento ou peca "
                f"ao admin para aumentar `QUERY_MAX_JOINS` em /api/admin/config."
            )
        for j in self.joins:
            if j.join_type not in _VALID_JOIN_TYPES:
                raise ProtheusError(
                    f"join_type '{j.join_type}' nao suportado (use INNER ou LEFT)"
                )
            if not is_known_alias(j.alias):
                raise ProtheusError(f"Alias de JOIN desconhecido: {j.alias!r}")
            if not j.on:
                raise ProtheusError(f"JOIN '{j.alias}' sem condicao ON")
            for cond in j.on:
                # left_alias deve referir-se a uma tabela ja conhecida (base
                # ou JOIN anterior na cadeia). Validacao final em _resolve.
                if not cond.left_alias or not cond.left_column or not cond.right_column:
                    raise ProtheusError(f"Condicao ON malformada em JOIN '{j.alias}'")

    # ---- Resolução de identificador ---------------------------------------

    def _sql_alias(self, logical: str) -> str:
        """Retorna o alias SQL (`t1`, `t2`...) para um alias lógico (`SC5`)."""
        logical = logical.upper()
        if logical not in self._aliases:
            raise ProtheusError(
                f"Alias '{logical}' nao está no FROM/JOIN — ajuste o payload"
            )
        return self._aliases[logical]["sql"]

    def _qualified(self, raw: str) -> str:
        """Resolve `"FOO"` (auto-prefixa base) ou `"ALIAS.FOO"` (qualificado).
        Retorna `t1.FOO` ou `tN.FOO` validado.
        """
        if "." in raw:
            m = _QUALIFIED_COL.match(raw)
            if not m:
                raise ProtheusError(f"Coluna qualificada invalida: {raw!r}")
            alias_logical, col = m.group(1).upper(), _safe_ident(m.group(2), "Coluna")
            return f"{self._sql_alias(alias_logical)}.{col}"
        # nao qualificado → assume base
        col = _safe_ident(raw, "Coluna")
        return f"t1.{col}"

    # ---- Construção das clausulas -----------------------------------------

    def _from_and_joins(self) -> str:
        # SQL Server NAO aceita `AS t1` apos um table hint. A ordem correta e':
        #     TABELA [alias] WITH (NOLOCK)
        # NAO:  TABELA WITH (NOLOCK) AS alias    ← erro "Incorrect syntax near AS"
        # NAO:  TABELA AS alias WITH (NOLOCK)    ← tambem rejeitado em alguns hints
        # Referencia: docs.microsoft.com/sql/t-sql/queries/hints-transact-sql-table
        base = self._aliases[self.base_alias]
        sql = f"{base['table']} {base['sql']} WITH (NOLOCK)"
        for j in self.joins:
            jt = j.join_type
            tgt_alias = j.alias.upper()
            tgt = self._aliases[tgt_alias]
            ons: list[str] = []
            for cond in j.on:
                left = self._qualified(f"{cond.left_alias}.{cond.left_column}")
                right_col = _safe_ident(cond.right_column, "Coluna ON direita")
                right = f"{tgt['sql']}.{right_col}"
                ons.append(f"{left} = {right}")
            on_clause = " AND ".join(ons)
            sql += f" {jt} JOIN {tgt['table']} {tgt['sql']} WITH (NOLOCK) ON {on_clause}"
        return sql

    def _where_clause(self) -> tuple[str, dict]:
        """WHERE engloba:
        - `D_E_L_E_T_ = ' '` para CADA tabela (base + joins).
        - Filtros do payload, com colunas qualificadas pelo alias SQL.
        """
        params: dict[str, Any] = {}
        # 1) D_E_L_E_T_ por tabela
        delet_parts: list[str] = []
        for info in self._aliases.values():
            delet_parts.append(f"{info['sql']}.D_E_L_E_T_ = ' '")
        # 2) Filtros do payload — adapta a logica de protheus_api._build_filter_clause
        filter_parts: list[str] = []
        for raw_key, value in self.filters.items():
            if "__" in raw_key:
                field, op = raw_key.rsplit("__", 1)
                if op not in _OP_SUFFIXES:
                    field, op = raw_key, "eq"
            else:
                field, op = raw_key, "eq"
            col_sql = self._qualified(field)
            bind = f"f_{len(params)}"
            if op == "eq":
                filter_parts.append(f"{col_sql} = :{bind}");  params[bind] = value
            elif op == "ne":
                filter_parts.append(f"{col_sql} <> :{bind}"); params[bind] = value
            elif op == "gt":
                filter_parts.append(f"{col_sql} > :{bind}");  params[bind] = value
            elif op == "gte":
                filter_parts.append(f"{col_sql} >= :{bind}"); params[bind] = value
            elif op == "lt":
                filter_parts.append(f"{col_sql} < :{bind}");  params[bind] = value
            elif op == "lte":
                filter_parts.append(f"{col_sql} <= :{bind}"); params[bind] = value
            elif op in ("like", "ilike"):
                filter_parts.append(f"{col_sql} LIKE :{bind}")
                params[bind] = f"%{value}%"
            elif op == "in":
                if not isinstance(value, list) or not value:
                    raise ProtheusError(f"Filtro {raw_key!r} espera lista nao-vazia")
                placeholders = []
                for i, v in enumerate(value):
                    k = f"{bind}_{i}"
                    placeholders.append(f":{k}");  params[k] = v
                filter_parts.append(f"{col_sql} IN ({', '.join(placeholders)})")
            elif op == "between":
                if not isinstance(value, list) or len(value) != 2:
                    raise ProtheusError(f"Filtro {raw_key!r} espera lista [min, max]")
                filter_parts.append(f"{col_sql} BETWEEN :{bind}_a AND :{bind}_b")
                params[f"{bind}_a"], params[f"{bind}_b"] = value
            else:
                raise ProtheusError(f"Operador nao suportado: {op}")

        all_parts = delet_parts + filter_parts
        return " AND ".join(all_parts), params

    def _select_cols(self) -> tuple[str, list[str]]:
        """Retorna (`SELECT ...` clause, lista de aliases de output).

        Cada coluna no SELECT vira `t1.FOO AS [SC5__FOO]` para que o resultado
        final venha com nomes nao-ambiguos `SC5__FOO`, `SA1__A1_NOME`.

        Se `self.columns` for vazio:
        - Sem JOIN: `SELECT *` da base (compat com query_table single).
        - Com JOIN: REJEITA (forca o usuario a escolher para evitar payloads
          explosivos com SELECT * de N tabelas).
        """
        if not self.columns:
            if not self.joins:
                return "*", ["*"]
            raise ProtheusError(
                "Com JOIN, voce precisa escolher quais colunas exibir "
                "(deixar em branco pega todas as colunas de todas as tabelas "
                "— pode estourar memoria e gerar nomes ambiguos)."
            )

        parts: list[str] = []
        output_names: list[str] = []
        for raw in self.columns:
            if "." in raw:
                m = _QUALIFIED_COL.match(raw)
                if not m:
                    raise ProtheusError(f"Coluna invalida no SELECT: {raw!r}")
                alias_logical = m.group(1).upper()
                col = _safe_ident(m.group(2), "Coluna SELECT")
                sql = f"{self._sql_alias(alias_logical)}.{col}"
                out = f"{alias_logical}__{col}"
            else:
                col = _safe_ident(raw, "Coluna SELECT")
                sql = f"t1.{col}"
                out = f"{self.base_alias}__{col}"
            parts.append(f"{sql} AS [{out}]")
            output_names.append(out)
        return ", ".join(parts), output_names

    # ---- API publica -------------------------------------------------------

    def build(self, page: int, page_size: int) -> tuple[str, str, dict, list[str]]:
        """Retorna `(data_sql, count_sql, params, output_columns)`.

        `output_columns` indica os nomes das colunas no resultado — usado pelo
        caller para exibir cabeçalho na grid (ordem preservada).
        """
        page = max(int(page or 1), 1)
        page_size = max(min(int(page_size or 100), 50000), 1)
        offset = (page - 1) * page_size

        cols_sql, output = self._select_cols()
        from_clause = self._from_and_joins()
        where_sql, params = self._where_clause()

        # ORDER BY estavel: R_E_C_N_O_ da BASE. Sempre existe em tabelas Protheus.
        order_by = "t1.R_E_C_N_O_"
        data_sql = (
            f"SELECT {cols_sql} "
            f"FROM {from_clause} "
            f"WHERE {where_sql} "
            f"ORDER BY {order_by} "
            f"OFFSET :_offset ROWS FETCH NEXT :_limit ROWS ONLY"
        )
        count_sql = f"SELECT COUNT_BIG(1) FROM {from_clause} WHERE {where_sql}"

        # Bind params: junta filtros + pag
        bind_data = {**params, "_offset": offset, "_limit": page_size}
        return data_sql, count_sql, bind_data, output


def run_join_query(
    engine,
    base: tuple[str, str],
    joins: list[JoinSpec],
    columns: list[str] | None,
    filters: dict | None,
    page: int,
    page_size: int,
    table_suffix: str = "0",
) -> dict:
    """Executa a query construida pelo JoinQueryBuilder. Retorna o mesmo
    formato de `protheus_api.query_table`: `{rows, total}`.

    `rows` ja vem com chaves no formato `SC5__C5_NUM` (alias_logico + '__' + coluna).
    """
    import math
    import pandas as pd
    from sqlalchemy import text
    from .protheus_api import _normalize_row

    b = JoinQueryBuilder(
        base=base, joins=joins, columns=columns, filters=filters,
        table_suffix=table_suffix,
    )
    data_sql, count_sql, params, output_cols = b.build(page, page_size)
    count_params = {k: v for k, v in params.items() if k not in ("_offset", "_limit")}

    try:
        with engine.connect() as conn:
            df = pd.read_sql(text(data_sql), conn, params=params)
            total = conn.execute(text(count_sql), count_params).scalar() or 0
    except Exception as exc:
        logger.exception("Falha em JoinQueryBuilder.run (data_sql=%s)", data_sql)
        raise ProtheusError(f"Falha na query com JOIN: {exc}") from exc

    rows = [_normalize_row(r) for r in df.to_dict(orient="records")]
    return {"rows": rows, "total": int(total), "output_columns": output_cols}
