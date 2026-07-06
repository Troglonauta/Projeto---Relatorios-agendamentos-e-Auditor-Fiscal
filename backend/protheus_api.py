"""Acesso direto ao banco SQL Server do Protheus (sem REST/AdvPL).

Estratégia:
- Engine SQLAlchemy única consumindo `PROTHEUS_DB_URL` direto do .env.
- Construção de SELECT parametrizado com sanitização de identificadores.
- Toda query inclui obrigatoriamente `D_E_L_E_T_ = ' '` (regra Protheus:
  exclusão lógica). Tabelas e colunas vêm do frontend e são validadas
  contra um regex restrito antes de entrarem na string SQL — valores
  sempre vão como bind parameter, nunca por concatenação.

Notas operacionais:
- O `R_E_C_N_O_` existe em 100% das tabelas Protheus e é usado como
  ORDER BY default para garantir paginação estável (OFFSET/FETCH).
- `WITH (NOLOCK)` evita contenção em horário de produção; remova se sua
  política proibir leitura suja.
- Na primeira inicialização da engine, a lista de drivers ODBC visíveis
  ao Python é logada — diagnóstico imediato para erros IM002.
"""
from __future__ import annotations

import logging
import math
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .config import get_settings
from .security import settings_store

logger = logging.getLogger(__name__)
settings = get_settings()

# Defaults de pool — sobrescritos por AppSetting('PROTHEUS_POOL_*') em runtime.
DEFAULT_POOL_SIZE = 20
DEFAULT_MAX_OVERFLOW = 30
DEFAULT_POOL_RECYCLE = 1800   # segundos
DEFAULT_POOL_TIMEOUT = 10     # segundos


class ProtheusError(RuntimeError):
    """Erro padrão da camada Protheus — capturado pelos routers como 502."""


# ---- Sanitização de identificadores -----------------------------------------

# Identificador SQL aceito: começa com letra, segue com letras/dígitos/underscore.
# Limite de 60 chars cobre nomes Protheus (3+3) e qualquer alias razoável.
_IDENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,59}$")

# Sufixos de operadores aceitos em filtros vindos do front.
_OP_SUFFIXES = {"eq", "ne", "gt", "gte", "lt", "lte", "like", "ilike", "in", "between"}


def _safe_ident(name: str, kind: str) -> str:
    """Valida nome de tabela/coluna. Devolve em UPPER (padrão Protheus).

    Lança `ProtheusError` se contiver algo fora de [A-Za-z0-9_] ou se
    começar com dígito — proteção primária contra SQL Injection já que
    identificadores **nunca** podem ser passados como bind parameter.
    """
    if not isinstance(name, str) or not _IDENT_RE.match(name):
        raise ProtheusError(f"{kind} inválido: {name!r}")
    return name.upper()


def _strip_qualifier(name: str) -> str:
    """Hotfix2 v2.0 — em consulta single-table, o frontend pode enviar
    coluna qualificada (`SF1.F1_FILIAL`) por inércia do Builder com JOINs.
    Como no single-table NAO existe alias SQL, removemos o prefixo antes
    do `_safe_ident`.

    Aceita também o formato JOIN-qualified Sprint 5 (`SF1__F1_FILIAL`) — esse
    NÃO é stripado aqui (o backend de JOIN trata).
    """
    if not isinstance(name, str):
        return name
    # Se o nome tem ponto, mantém só a parte depois do ponto (sufixo da coluna).
    if "." in name and "__" not in name:
        return name.split(".", 1)[1]
    return name


# ---- Engine Registry (singleton thread-safe com hot-reload) -----------------

class _EngineRegistry:
    """Singleton thread-safe da engine Protheus.

    - `get()` cria sob demanda, lendo URL e pool params do `settings_store`.
    - `reset()` faz `dispose()` da engine atual e zera o cache — proximo
      `get()` reconstroi com os novos valores. Usado pelo endpoint
      `/api/admin/reload-config`.
    """
    def __init__(self) -> None:
        self._engine: Optional[Engine] = None
        self._lock = threading.Lock()

    def _build(self) -> Engine:
        _log_available_drivers()
        url = settings_store.get_setting("PROTHEUS_DB_URL") or settings.PROTHEUS_DB_URL
        pool_size = int(settings_store.get_setting("PROTHEUS_POOL_SIZE", DEFAULT_POOL_SIZE))
        max_overflow = int(settings_store.get_setting("PROTHEUS_MAX_OVERFLOW", DEFAULT_MAX_OVERFLOW))
        pool_recycle = int(settings_store.get_setting("PROTHEUS_POOL_RECYCLE", DEFAULT_POOL_RECYCLE))
        pool_timeout = int(settings_store.get_setting("PROTHEUS_POOL_TIMEOUT", DEFAULT_POOL_TIMEOUT))

        engine = create_engine(
            url,
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_recycle=pool_recycle,
            pool_timeout=pool_timeout,
            fast_executemany=True,
            future=True,
        )
        logger.info(
            "Engine Protheus inicializada (pool_size=%d max_overflow=%d recycle=%ds timeout=%ds).",
            pool_size, max_overflow, pool_recycle, pool_timeout,
        )
        return engine

    def get(self) -> Engine:
        if self._engine is None:
            with self._lock:
                if self._engine is None:
                    self._engine = self._build()
        return self._engine

    def reset(self) -> None:
        """Descarta a engine atual. Proximo get() reconstroi com settings novos."""
        with self._lock:
            if self._engine is not None:
                try:
                    self._engine.dispose()
                except Exception as exc:
                    logger.warning("Falha em dispose() da engine: %s", exc)
            self._engine = None
            logger.info("Engine Protheus resetada.")


engine_registry = _EngineRegistry()


def _log_available_drivers() -> None:
    """Loga os drivers ODBC visíveis ao processo Python."""
    try:
        import pyodbc
        drivers = pyodbc.drivers()
        logger.info("Drivers ODBC visiveis ao Python: %s", drivers)
        if not any("SQL Server" in d for d in drivers):
            logger.warning(
                "Nenhum driver 'SQL Server' detectado. Instale o "
                "'ODBC Driver 17 for SQL Server' (64-bit)."
            )
    except Exception as exc:
        logger.warning("Nao consegui listar drivers ODBC: %s", exc)


def get_engine() -> Engine:
    """Compat: API antiga continua valendo via registry."""
    return engine_registry.get()


def reset_engine() -> None:
    """Compat: hook publico para /api/admin/reload-config."""
    engine_registry.reset()


# ---- Filtros -> SQL parametrizado -------------------------------------------

def _build_filter_clause(filters: Dict[str, Any]) -> Tuple[str, dict]:
    """Converte `{"E5_FILIAL": "01", "E5_DATA__gte": "20260101"}` em
    fragmento SQL parametrizado + dict de binds.

    Operadores suportados (sufixo após `__`):
        eq   (default)   campo = :v
        ne               campo <> :v
        gt | gte         > / >=
        lt | lte         < / <=
        like | ilike     LIKE %v%   (SQL Server é case-insensitive por collation)
        in               campo IN (...lista...)
        between          campo BETWEEN :a AND :b   (espera lista [a, b])
    """
    if not filters:
        return "", {}

    parts: List[str] = []
    params: dict = {}

    for raw_key, value in filters.items():
        if "__" in raw_key:
            field, op = raw_key.rsplit("__", 1)
            if op not in _OP_SUFFIXES:
                # se o sufixo não é operador, era parte do nome -> trate como eq
                field, op = raw_key, "eq"
        else:
            field, op = raw_key, "eq"

        # Hotfix2 v2.0 — aceita coluna qualificada `SF1.F1_FILIAL` em single-table
        # (frontend Builder emite assim mesmo sem JOINs).
        field = _safe_ident(_strip_qualifier(field), "Coluna em filtro")
        bind = f"f_{len(params)}"

        if op == "eq":
            parts.append(f"{field} = :{bind}")
            params[bind] = value
        elif op == "ne":
            parts.append(f"{field} <> :{bind}")
            params[bind] = value
        elif op == "gt":
            parts.append(f"{field} > :{bind}")
            params[bind] = value
        elif op == "gte":
            parts.append(f"{field} >= :{bind}")
            params[bind] = value
        elif op == "lt":
            parts.append(f"{field} < :{bind}")
            params[bind] = value
        elif op == "lte":
            parts.append(f"{field} <= :{bind}")
            params[bind] = value
        elif op in ("like", "ilike"):
            parts.append(f"{field} LIKE :{bind}")
            params[bind] = f"%{value}%"
        elif op == "in":
            if not isinstance(value, list) or not value:
                raise ProtheusError(f"Filtro `{raw_key}` espera lista nao-vazia")
            placeholders = []
            for i, v in enumerate(value):
                key = f"{bind}_{i}"
                placeholders.append(f":{key}")
                params[key] = v
            parts.append(f"{field} IN ({', '.join(placeholders)})")
        elif op == "between":
            if not isinstance(value, list) or len(value) != 2:
                raise ProtheusError(f"Filtro `{raw_key}` espera lista [min, max]")
            parts.append(f"{field} BETWEEN :{bind}_a AND :{bind}_b")
            params[f"{bind}_a"], params[f"{bind}_b"] = value
        else:
            raise ProtheusError(f"Operador nao suportado: {op}")

    return " AND ".join(parts), params


# ---- Normalização do resultado ----------------------------------------------

def _normalize_row(row: dict) -> dict:
    """JSON-friendly: NaN -> None; strings com trailing space (CHAR Protheus)
    ganham `rstrip` para reduzir ruído visual no front."""
    out = {}
    for k, v in row.items():
        if v is None:
            out[k] = None
        elif isinstance(v, float) and math.isnan(v):
            out[k] = None
        elif isinstance(v, str):
            out[k] = v.rstrip()
        else:
            out[k] = v
    return out


# ---- API pública -------------------------------------------------------------

def query_table(
    table: str,
    columns: Optional[List[str]] = None,
    filters: Optional[Dict[str, Any]] = None,
    page: int = 1,
    page_size: int = 100,
) -> Dict[str, Any]:
    """Executa SELECT seguro e paginado em uma tabela do Protheus.

    Retorna `{"rows": [...], "total": N}` — total ignora paginação.

    Regras:
    - Identificadores (tabela/colunas/filtros) validados via regex.
    - `D_E_L_E_T_ = ' '` sempre presente no WHERE (registro nao excluido).
    - Valores entram como bind parameters (SQLAlchemy `text(...)`).
    """
    table = _safe_ident(table, "Tabela")

    if columns:
        # Hotfix2 v2.0 — single-table: strip `ALIAS.` se vier qualificado
        validated_cols = [_safe_ident(_strip_qualifier(c), "Coluna") for c in columns]
        cols_sql = ", ".join(validated_cols)
    else:
        cols_sql = "*"

    filter_sql, params = _build_filter_clause(filters or {})

    where = "D_E_L_E_T_ = ' '"
    if filter_sql:
        where = f"{where} AND {filter_sql}"

    page = max(int(page or 1), 1)
    page_size = max(min(int(page_size or 100), 50000), 1)
    offset = (page - 1) * page_size

    sql = (
        f"SELECT {cols_sql} "
        f"FROM {table} WITH (NOLOCK) "
        f"WHERE {where} "
        f"ORDER BY R_E_C_N_O_ "
        f"OFFSET :_offset ROWS FETCH NEXT :_limit ROWS ONLY"
    )
    count_sql = f"SELECT COUNT_BIG(1) AS total FROM {table} WITH (NOLOCK) WHERE {where}"

    bind_params = {**params, "_offset": offset, "_limit": page_size}
    count_params = dict(params)  # COUNT nao usa offset/limit

    engine = get_engine()
    try:
        with engine.connect() as conn:
            df = pd.read_sql(text(sql), conn, params=bind_params)
            total = conn.execute(text(count_sql), count_params).scalar() or 0
    except ProtheusError:
        raise
    except Exception as exc:
        logger.exception("Falha ao executar SELECT em %s", table)
        # Hotfix Sprint 8 Part 4 — detecta pyodbc.ProgrammingError ("Invalid
        # column name") e levanta com mensagem mais amigavel. O caller
        # (download/query) propaga como 502, mas o usuario sabe exatamente
        # qual coluna esta errada em vez de receber stack trace.
        msg = str(exc)
        if "Invalid column name" in msg or type(exc).__name__ == "ProgrammingError":
            # Extrai o nome da coluna da mensagem do SQL Server quando possivel
            import re as _re
            m = _re.search(r"Invalid column name ['\"]([^'\"]+)['\"]", msg)
            col = m.group(1) if m else "(desconhecida)"
            raise ProtheusError(
                f"Coluna inexistente em {table}: '{col}'. "
                f"Algumas colunas do dicionario nao existem fisicamente "
                f"nesta release do Protheus. Remova essa coluna da consulta."
            ) from exc
        raise ProtheusError(f"Falha ao consultar {table}: {exc}") from exc

    # Hotfix Sprint 8 Part 4 — garantia defensiva: nunca retorna None.
    if df is None or df.empty:
        return {"rows": [], "total": int(total)}
    rows = [_normalize_row(r) for r in df.to_dict(orient="records")]
    return {"rows": rows, "total": int(total)}


def test_connection() -> dict:
    """Health-check: bate `SELECT 1` para validar string e credenciais."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1")).scalar()
        return {"ok": True}
    except Exception as exc:
        logger.exception("test_connection falhou")
        return {"ok": False, "error": str(exc)}


def list_db_tables(prefix: Optional[str] = None) -> List[str]:
    """Lista tabelas do schema dbo. Util para autocompletar tabelas no front.

    Aceita prefixo opcional (ex: 'SE5' devolve SE5010, SE5020, ...).
    """
    sql = (
        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_TYPE='BASE TABLE'"
    )
    params: dict = {}
    if prefix:
        prefix = _safe_ident(prefix, "Prefixo")
        sql += " AND TABLE_NAME LIKE :p"
        params["p"] = f"{prefix}%"
    sql += " ORDER BY TABLE_NAME"
    with get_engine().connect() as conn:
        return [r[0] for r in conn.execute(text(sql), params).all()]


def available_odbc_drivers() -> List[str]:
    """Devolve a lista de drivers ODBC que o Python enxerga.

    Util quando aparece IM002 ou DSN nao encontrada — exposto como endpoint
    em `/api/protheus/drivers` para diagnostico em produção.
    """
    try:
        import pyodbc
        return list(pyodbc.drivers())
    except Exception as exc:
        return [f"erro ao listar drivers: {exc}"]


# ---- Catálogo de colunas / filiais ------------------------------------------

def list_table_columns(table: str) -> List[Dict[str, Any]]:
    """Devolve a lista de colunas físicas de uma tabela do Protheus.

    Cada item: `{"name": "E5_FILIAL", "type": "varchar", "max_length": 6}`.
    Filtramos colunas internas Protheus (D_E_L_E_T_, R_E_C_N_O_, R_E_C_D_E_L_)
    do retorno padrão para não poluir o construtor visual.
    """
    table = _safe_ident(table, "Tabela")
    sql = (
        "SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH "
        "FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_NAME = :t "
        "ORDER BY ORDINAL_POSITION"
    )
    INTERNAL = {"D_E_L_E_T_", "R_E_C_N_O_", "R_E_C_D_E_L_"}
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(text(sql), {"t": table}).all()
    except Exception as exc:
        logger.exception("Falha ao listar colunas de %s", table)
        raise ProtheusError(f"Falha ao listar colunas de {table}: {exc}") from exc
    return [
        {
            "name": r[0],
            "type": (r[1] or "").lower(),
            "max_length": int(r[2]) if r[2] is not None else None,
        }
        for r in rows
        if r[0] not in INTERNAL
    ]


def list_branches(table: str, branch_field: str) -> List[str]:
    """Lista os códigos de filial DISTINTOS encontrados na tabela.

    Normalizacao: Protheus pode guardar 2, 4, 6 ou 8 chars na coluna `*_FILIAL`
    (depende da configuracao `MV_CFLI`). Para montar o nome fisico da tabela,
    SEMPRE usamos os 2 primeiros chars (que e' o cFil real). Sem isso, valores
    "0101" virariam nomes invalidos tipo "SE501010".

    Ex.: `list_branches("SE1010", "E1_FILIAL")` -> `["01", "02", "10"]`
    (mesmo que a coluna fisica tenha "01      ", "0101", "01020001", etc).

    Retorna vazio se a coluna não existir (não é erro fatal).
    """
    table = _safe_ident(table, "Tabela")
    branch_field = _safe_ident(branch_field, "Campo de filial")
    sql = (
        f"SELECT DISTINCT LTRIM(RTRIM({branch_field})) AS b "
        f"FROM {table} WITH (NOLOCK) "
        f"WHERE D_E_L_E_T_ = ' ' AND {branch_field} IS NOT NULL "
        f"ORDER BY b"
    )
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(text(sql)).all()
    except Exception as exc:
        logger.warning("Não foi possível listar filiais de %s: %s", table, exc)
        return []

    # Normaliza: pega so os 2 primeiros chars, deduplica preservando ordem.
    seen = set()
    out: List[str] = []
    for r in rows:
        raw = (r[0] or "").strip()
        if not raw:
            continue
        normalized = raw[:2]
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return sorted(out)


def resolve_table_name(alias: str, branch: str, suffix: str = "0") -> str:
    """Monta o nome físico Protheus: ALIAS + FILIAL + sufixo.

    Ex.: ('SE1', '01', '0') -> 'SE1010'. Apenas os 2 primeiros chars da filial
    sao usados (cFil real do Protheus). Garante que valores como "0101"
    nao virem nomes fisicos invalidos.
    """
    alias = _safe_ident(alias, "Alias")
    branch = (branch or "").strip()[:2]   # <<< normaliza para 2 chars
    if not re.match(r"^[A-Za-z0-9]{1,2}$", branch):
        raise ProtheusError(f"Filial inválida: {branch!r}")
    if not re.match(r"^[A-Za-z0-9]{0,3}$", suffix or ""):
        raise ProtheusError(f"Sufixo inválido: {suffix!r}")
    return f"{alias}{branch}{suffix}".upper()
