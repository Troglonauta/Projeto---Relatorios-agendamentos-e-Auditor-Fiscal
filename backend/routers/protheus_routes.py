"""Consulta direta ao SQL Server do Protheus + download imediato.

Endpoints expostos:
- GET  /api/protheus/aliases   — catálogo (alias, label, branch_field) liberado a quem tem `view`.
- GET  /api/protheus/branches  — filiais DISTINCT da tabela física resolvida.
- GET  /api/protheus/columns   — colunas físicas da tabela (para o construtor visual).
- POST /api/protheus/query     — consulta paginada (suporta payload novo OU antigo).
- POST /api/protheus/download  — exporta xlsx/csv/pdf/ods.
- GET  /api/protheus/tables    — tabelas autorizadas para o usuário corrente.
- GET  /api/protheus/db-tables — tabelas físicas no banco (admin).
- GET  /api/protheus/test-connection / drivers — diagnóstico (admin).

Payload novo do `/query`:
    {
      "alias": "SE1",
      "branch": "01",
      "columns": ["E1_FILIAL", "E1_PREFIXO", ...],
      "filters": [
        {"field": "E1_VALOR", "op": "gte", "value": "100"},
        {"field": "E1_NATUREZ", "op": "contains", "value": "ALU"}
      ],
      "page": 1,
      "page_size": 100,
      "joins": []        # reservado p/ V2 multi-tabela (não usado ainda)
    }

Payload antigo (compat):
    { "table": "SE1010", "columns": [...], "filters": {"E1_NATUREZ__like": "ALU"} }
"""
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import audit, protheus_api, reports
from ..config import get_settings
from ..database import get_db
from ..deps import (
    assert_table_allowed, get_client_ip, get_current_user, require_action,
    require_admin,
)
from ..models import User
from ..models import TableProfile, UserProfile
from ..protheus_aliases import ALIASES, branch_field, is_known_alias
from ..protheus_api import ProtheusError
from ..schemas import ProtheusQueryRequest, ProtheusQueryResponse

router = APIRouter(prefix="/api/protheus", tags=["protheus"])
settings = get_settings()


# ---- Helpers ---------------------------------------------------------------

# Mapeia operador amigável (vindo do construtor visual) -> sufixo SQL.
OP_MAP = {
    "eq": "eq", "=": "eq", "igual": "eq", "equals": "eq",
    "ne": "ne", "<>": "ne", "diferente": "ne",
    "gt": "gt", ">": "gt", "maior": "gt",
    "gte": "gte", ">=": "gte", "maior_igual": "gte",
    "lt": "lt", "<": "lt", "menor": "lt",
    "lte": "lte", "<=": "lte", "menor_igual": "lte",
    "like": "like", "contem": "like", "contains": "like",
    "in": "in",
    "between": "between",
}


def _resolve_table(payload: ProtheusQueryRequest) -> str:
    """Decide o nome físico da tabela.

    Prioridade:
    1. `alias + branch` (novo, validado contra catálogo).
    2. `table` (compat — string já resolvida pelo cliente).
    """
    if payload.alias:
        if not is_known_alias(payload.alias):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Alias '{payload.alias}' não está no catálogo permitido",
            )
        if not payload.branch:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Informe a filial (branch) ao usar alias",
            )
        try:
            return protheus_api.resolve_table_name(
                payload.alias, payload.branch, settings.PROTHEUS_TABLE_SUFFIX,
            )
        except ProtheusError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    if payload.table:
        return payload.table.upper()

    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        "Informe `alias`+`branch` ou `table`",
    )


def _normalize_filters(payload: ProtheusQueryRequest) -> dict | None:
    """Converte filtros do formato 'visual' (lista de regras) para o formato
    interno do `protheus_api._build_filter_clause` (dict com sufixo `__op`).
    """
    if payload.rules:
        out: dict[str, Any] = {}
        for i, rule in enumerate(payload.rules):
            op = OP_MAP.get((rule.op or "eq").lower())
            if not op:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Operador desconhecido: {rule.op!r}",
                )
            # Permite múltiplas regras na mesma coluna sufixando com `#i`.
            key = rule.field if op == "eq" else f"{rule.field}__{op}"
            if key in out:
                key = f"{key}__r{i}"
            out[key] = rule.value
        return out

    return payload.filters


# ---- Catálogo / metadata ----------------------------------------------------

@router.get("/aliases", dependencies=[Depends(require_action("view"))])
def list_aliases(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Catalogo de aliases permitidos + perfis de cada alias (Fase 4.A).

    Resposta:
    {
      "aliases": [
        {"alias": "SE1", "label": "Contas a Receber", "branch_field": "E1_FILIAL",
         "profiles": ["FINANCEIRO"]}, ...
      ],
      "table_suffix": "0",
      "user_profiles": ["FINANCEIRO", "CONTABIL"]
    }

    Operador recebe apenas aliases acessiveis via:
    - whitelist direta (UserTablePermission) OU
    - algum perfil vinculado (UserProfile -> TableProfile)
    """
    # 1) Mapa alias -> [profile codes] (uma query)
    profile_by_alias: dict[str, list[str]] = {}
    rows = db.query(TableProfile.alias, TableProfile.profile_id).all()
    if rows:
        from ..models import Profile
        id_to_code = {p.id: p.code for p in db.query(Profile).all()}
        for alias, pid in rows:
            profile_by_alias.setdefault(alias.upper(), []).append(id_to_code.get(pid, ""))

    items = [
        {
            "alias": a, "label": lbl,
            "branch_field": branch_field(a),
            "profiles": sorted(profile_by_alias.get(a, [])),
        }
        for a, lbl in ALIASES
    ]

    # Perfis do user (para o frontend desenhar o filtro "Modulo").
    # Admin ve todos os perfis cadastrados — pode filtrar livremente.
    from ..models import Profile
    if user.role == "admin":
        user_profiles = sorted({p.code for p in db.query(Profile).all()})
    else:
        user_profiles = sorted({up.profile.code for up in (user.profile_links or []) if up.profile})

    # Filtro para operadores (admin ve tudo)
    if user.role != "admin":
        direct = {p.table_name.upper() for p in user.table_permissions}
        via_profile = set()
        for up in (user.profile_links or []):
            if up.profile:
                via_profile.update(t.alias.upper() for t in up.profile.tables)
        allowed = direct | via_profile
        items = [i for i in items if i["alias"] in allowed]

    return {
        "aliases": items,
        "table_suffix": settings.PROTHEUS_TABLE_SUFFIX,
        "user_profiles": user_profiles,
    }


@router.get("/branches", dependencies=[Depends(require_action("view"))])
def list_branches(
    alias: str = Query(..., description="Alias (3 letras), ex: SE1"),
    user: User = Depends(get_current_user),
):
    """Filiais DISTINCT encontradas na tabela física resolvida pelo alias.

    Tenta primeiro `ALIAS + '99' + sufixo` para encontrar a tabela física
    (Protheus normalmente usa o mesmo schema). Se não der, tenta `ALIAS + '01' + sufixo`.
    Se nada funcionar, devolve lista vazia.
    """
    alias = (alias or "").upper()
    if not is_known_alias(alias):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Alias desconhecido")
    assert_table_allowed(user, alias)

    field = branch_field(alias)
    if not field:
        return {"branches": []}

    suffix = settings.PROTHEUS_TABLE_SUFFIX
    candidates = [f"{alias}99{suffix}", f"{alias}01{suffix}", f"{alias}10{suffix}"]
    for table in candidates:
        try:
            branches = protheus_api.list_branches(table, field)
        except ProtheusError:
            continue
        if branches:
            return {"branches": branches, "resolved_table": table}
    return {"branches": [], "resolved_table": None}


@router.get("/columns", dependencies=[Depends(require_action("view"))])
def list_columns(
    alias: str = Query(..., description="Alias (3 letras), ex: SE1"),
    branch: str = Query(..., description="Código da filial, ex: 01"),
    user: User = Depends(get_current_user),
):
    """Colunas da tabela fisica resolvida (alias + branch + sufixo).

    Distingue 3 cenarios:
    - 200 + columns nao vazio → OK.
    - 404 (table_missing=true) → tabela fisica nao existe no SQL Server.
    - 200 + columns vazio (raro) → tabela existe mas sem colunas listaveis.
    """
    alias = (alias or "").upper()
    if not is_known_alias(alias):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Alias desconhecido")
    assert_table_allowed(user, alias)

    try:
        table = protheus_api.resolve_table_name(alias, branch, settings.PROTHEUS_TABLE_SUFFIX)
        cols = protheus_api.list_table_columns(table)
    except ProtheusError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))

    if not cols:
        # Tabela fisica nao existe no schema — Protheus ainda nao criou.
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Tabela '{table}' nao existe no banco. "
            f"O Protheus cria a tabela na primeira escrita; "
            f"se voce esperava dados, confira a filial e o sufixo "
            f"em Administracao > Configuracoes."
        )
    return {"table": table, "columns": _enrich_columns(cols)}


@router.get("/columns/{table_name}", dependencies=[Depends(require_action("view"))])
def list_columns_by_table(table_name: str, user: User = Depends(get_current_user)):
    """Variante crua: recebe o nome físico já resolvido (ex.: SE1010)."""
    table_name = (table_name or "").upper()
    # Inferência do alias para conferir whitelist.
    if len(table_name) >= 3:
        assert_table_allowed(user, table_name[:3])
    try:
        cols = protheus_api.list_table_columns(table_name)
    except ProtheusError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    return {"table": table_name, "columns": _enrich_columns(cols)}


# ---- Query / Download -------------------------------------------------------

def _columns_human(rows: list) -> dict:
    """Mapa {coluna_fisica: titulo_humano} via SX3 para o cabecalho do grid.
    Se a SX3 nao tiver o campo, o titulo retorna igual ao fisico (sem mudanca)."""
    if not rows or not isinstance(rows[0], dict):
        return {}
    from .. import dict_sx3
    return {col: dict_sx3.humanize_title(col) for col in rows[0].keys()}


def _enrich_columns(cols: list) -> list:
    """Acrescenta `description` (titulo SX3) a cada coluna, para o seletor de
    colunas e o dropdown de filtros exibirem o nome humanizado. Vazio se a SX3
    nao tiver o campo (frontend cai para o nome fisico)."""
    from .. import dict_sx3
    for c in cols:
        name = c.get("name", "")
        title = dict_sx3.humanize_title(name)
        c["description"] = title if title and title != name else ""
    return cols


@router.post(
    "/query",
    response_model=ProtheusQueryResponse,
    dependencies=[Depends(require_action("view"))],
)
def query(
    payload: ProtheusQueryRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Consulta paginada — single-table ou multi-table com JOINs (Sprint 5).

    Quando `payload.joins` esta presente, usa `JoinQueryBuilder` (motor novo).
    Caso contrario, mantem o fluxo single-table (compat total com Fase 2-4).
    """
    # === Rota multi-table (JOIN) — Sprint 5 ==================================
    if payload.joins:
        if not payload.alias or not payload.branch:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Com JOIN, `alias` e `branch` da tabela base sao obrigatorios",
            )
        # Whitelist por ALIAS aplicada a base E a todas as tabelas dos JOINs
        assert_table_allowed(user, payload.alias.upper())
        for j in payload.joins:
            assert_table_allowed(user, j.alias.upper())

        # Converte schemas Pydantic -> dataclasses do builder
        from ..query_engine import JoinCond, JoinSpec, run_join_query
        join_specs = [
            JoinSpec(
                alias=j.alias.upper(), branch=j.branch,
                join_type=j.join_type.upper(),
                on=[JoinCond(c.left_alias.upper(), c.left_column.upper(),
                             c.right_column.upper()) for c in j.on],
            )
            for j in payload.joins
        ]
        # Filtros — para JOIN aceita colunas qualificadas (`SC5.C5_EMISSAO__gte`).
        # _normalize_filters ja prepara o dict com sufixos `__op`.
        filters = _normalize_filters(payload)
        try:
            result = run_join_query(
                protheus_api.engine_registry.get(),
                base=(payload.alias.upper(), payload.branch),
                joins=join_specs,
                columns=payload.columns,
                filters=filters,
                page=payload.page,
                page_size=payload.page_size,
                table_suffix=settings.PROTHEUS_TABLE_SUFFIX,
            )
        except ProtheusError as exc:
            audit.log(db, action="protheus.query.join.failed", user=user,
                      ip=get_client_ip(request), detail=str(exc), success=False)
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Falha no JOIN: {exc}")

        audit.log(
            db, action="protheus.query.join", user=user, ip=get_client_ip(request),
            detail=f"base={payload.alias}+{payload.branch} joins={len(join_specs)} "
                   f"cols={len(payload.columns or [])} rows={result['total']}",
        )
        # Sprint 8 Part 3 — LGPD: mascara colunas sensiveis se user nao for admin
        from .. import lgpd
        if lgpd.should_mask_for(user):
            lgpd.apply_to_rows(result["rows"], user)
        return ProtheusQueryResponse(
            table=f"{payload.alias.upper()}{payload.branch}{settings.PROTHEUS_TABLE_SUFFIX} (+{len(join_specs)} JOIN)",
            rows=result["rows"],
            total=result["total"],
            page=payload.page,
            page_size=payload.page_size,
            columns_human=_columns_human(result["rows"]),
        )

    # === Rota single-table (compat) =========================================
    table = _resolve_table(payload)
    alias_for_acl = (payload.alias or table[:3]).upper()
    assert_table_allowed(user, alias_for_acl)

    try:
        filters = _normalize_filters(payload)
        result = protheus_api.query_table(
            table=table,
            columns=payload.columns,
            filters=filters,
            page=payload.page,
            page_size=payload.page_size,
        )
    except ProtheusError as exc:
        audit.log(
            db, action="protheus.query.failed", user=user, ip=get_client_ip(request),
            detail=f"{table}: {exc}", success=False,
        )
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Falha no Protheus: {exc}")

    audit.log(
        db, action="protheus.query", user=user, ip=get_client_ip(request),
        detail=f"table={table} rows={result['total']}",
    )
    # Sprint 8 Part 3 — LGPD: mascara colunas sensiveis se user nao for admin
    from .. import lgpd
    if lgpd.should_mask_for(user):
        lgpd.apply_to_rows(result["rows"], user)
    return ProtheusQueryResponse(
        table=table,
        rows=result["rows"],
        total=result["total"],
        page=payload.page,
        page_size=payload.page_size,
        columns_human=_columns_human(result["rows"]),
    )


@router.post("/download", dependencies=[Depends(require_action("export"))])
def download(
    payload: ProtheusQueryRequest,
    file_format: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Download de relatorio em XLSX/CSV/PDF/ODS.

    Hotfix Sprint 10 — BUGFIX CRITICO: antes a rota chamava direto
    `protheus_api.query_table()` (single-table), IGNORANDO `payload.joins`
    completamente. Resultado: `SELECT D1_COD FROM SF1010` quebrava com
    `Invalid column name 'D1_COD'` (502 Bad Gateway) sempre que o usuario
    tentava baixar um relatorio com cruzamento de tabelas.

    Agora o dispatch e' identico ao `/query`: se `payload.joins` existe,
    usa `run_join_query` (mesmo motor SQL); senao, single-table compat.
    """
    filters = _normalize_filters(payload)

    # === Rota multi-table (JOIN) — Sprint 10 hotfix ===========================
    if payload.joins:
        if not payload.alias or not payload.branch:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Com JOIN, `alias` e `branch` da tabela base sao obrigatorios",
            )
        # Whitelist por ALIAS aplicada a base E a todas as tabelas dos JOINs
        assert_table_allowed(user, payload.alias.upper())
        for j in payload.joins:
            assert_table_allowed(user, j.alias.upper())

        from ..query_engine import JoinCond, JoinSpec, run_join_query
        join_specs = [
            JoinSpec(
                alias=j.alias.upper(), branch=j.branch,
                join_type=j.join_type.upper(),
                on=[JoinCond(c.left_alias.upper(), c.left_column.upper(),
                             c.right_column.upper()) for c in j.on],
            )
            for j in payload.joins
        ]
        try:
            # page_size grande no download para nao precisar paginar
            page_size = max(payload.page_size or 0, 5000)
            result = run_join_query(
                protheus_api.engine_registry.get(),
                base=(payload.alias.upper(), payload.branch),
                joins=join_specs,
                columns=payload.columns,
                filters=filters,
                page=1,
                page_size=page_size,
                table_suffix=settings.PROTHEUS_TABLE_SUFFIX,
            )
        except ProtheusError as exc:
            audit.log(db, action="protheus.download.join.failed", user=user,
                      ip=get_client_ip(request), detail=str(exc), success=False)
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, f"Falha no JOIN: {exc}"
            )

        from .. import lgpd
        if lgpd.should_mask_for(user):
            lgpd.apply_to_rows(result["rows"], user)

        try:
            data, mime, ext = reports.to_bytes(
                result["rows"], file_format,
                include_physical=(user.role == "admin"),   # fisico so p/ admin
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

        # Nome do arquivo refletindo o JOIN (base + N joins)
        base_table = (
            f"{payload.alias.upper()}{payload.branch}"
            f"{settings.PROTHEUS_TABLE_SUFFIX}_join{len(join_specs)}"
        )
        filename = f"{base_table}_{datetime.now():%Y%m%d_%H%M%S}.{ext}"
        audit.log(
            db, action="protheus.download.join", user=user, ip=get_client_ip(request),
            detail=f"base={payload.alias}+{payload.branch} joins={len(join_specs)} "
                   f"format={ext} rows={result['total']}",
        )
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        return StreamingResponse(iter([data]), media_type=mime, headers=headers)

    # === Rota single-table (compat — comportamento original) =================
    table = _resolve_table(payload)
    alias_for_acl = (payload.alias or table[:3]).upper()
    assert_table_allowed(user, alias_for_acl)

    try:
        result = protheus_api.query_table(
            table=table,
            columns=payload.columns,
            filters=filters,
            page=1,
            page_size=max(payload.page_size, 5000),
        )
    except ProtheusError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Falha no Protheus: {exc}")

    # Sprint 8 Part 3 — LGPD: mascara antes de serializar para XLSX/CSV/PDF
    from .. import lgpd
    if lgpd.should_mask_for(user):
        lgpd.apply_to_rows(result["rows"], user)

    try:
        data, mime, ext = reports.to_bytes(
            result["rows"], file_format,
            include_physical=(user.role == "admin"),   # fisico so p/ admin
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    filename = f"{table}_{datetime.now():%Y%m%d_%H%M%S}.{ext}"
    audit.log(
        db, action="protheus.download", user=user, ip=get_client_ip(request),
        detail=f"table={table} format={ext} rows={result['total']}",
    )
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(iter([data]), media_type=mime, headers=headers)


# ---- Permissões / diagnóstico ----------------------------------------------

@router.get("/tables", dependencies=[Depends(require_action("view"))])
def my_tables(user: User = Depends(get_current_user)):
    if user.role == "admin":
        return {"tables": [], "unrestricted": True}
    return {
        "tables": sorted({p.table_name for p in user.table_permissions}),
        "unrestricted": False,
    }


@router.get("/db-tables", dependencies=[Depends(require_admin)])
def db_tables(prefix: str | None = Query(None, description="Filtra por prefixo, ex: SE5")):
    try:
        return {"tables": protheus_api.list_db_tables(prefix=prefix)}
    except ProtheusError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))


@router.get("/test-connection", dependencies=[Depends(require_admin)])
def test_connection():
    return protheus_api.test_connection()


@router.get("/drivers", dependencies=[Depends(require_admin)])
def odbc_drivers():
    return {"drivers": protheus_api.available_odbc_drivers()}
