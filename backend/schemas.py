"""Schemas Pydantic — contratos de entrada/saída da API.

Mantemos schemas separados dos modelos ORM para que a forma exposta na API
não dependa do schema do banco.
"""
from datetime import datetime
from typing import List, Optional, Literal
from pydantic import BaseModel, EmailStr, Field, ConfigDict


# ---- Auth ---------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    must_change_password: bool = False
    user: "UserOut"


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=72)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


# ---- User / RBAC --------------------------------------------------------------

ActionLiteral = Literal["view", "export", "schedule", "fiscal"]
RoleLiteral = Literal["admin", "operator"]


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=60)
    email: EmailStr
    full_name: Optional[str] = None
    password: str = Field(min_length=8, max_length=72)
    role: RoleLiteral = "operator"
    allowed_tables: List[str] = []
    allowed_actions: List[ActionLiteral] = ["view"]
    allowed_profiles: List[str] = []   # Fase 4 — codes dos perfis vinculados


class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    role: Optional[RoleLiteral] = None
    is_active: Optional[bool] = None
    allowed_tables: Optional[List[str]] = None
    allowed_actions: Optional[List[ActionLiteral]] = None
    allowed_profiles: Optional[List[str]] = None   # Fase 4


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    email: EmailStr
    full_name: Optional[str] = None
    role: str
    is_active: bool
    must_change_password: bool
    last_login_at: Optional[datetime] = None
    created_at: Optional[datetime] = None      # v2.21 — data de criacao (BRT)
    created_by: Optional[str] = None           # v2.21 — quem criou
    deleted_at: Optional[datetime] = None      # v2.22 — inativado em (soft delete)
    allowed_tables: List[str] = []
    allowed_actions: List[str] = []
    allowed_profiles: List[str] = []   # Fase 4 — codes dos perfis vinculados


# ---- Perfis (Fase 4.A) -------------------------------------------------------

class ProfileCreate(BaseModel):
    code: str = Field(min_length=2, max_length=30, pattern=r"^[A-Z][A-Z0-9_]*$")
    label: str = Field(min_length=2, max_length=60)
    description: Optional[str] = None


class ProfileUpdate(BaseModel):
    label: Optional[str] = Field(default=None, min_length=2, max_length=60)
    description: Optional[str] = None


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    label: str
    description: Optional[str] = None
    created_at: datetime
    tables: List[str] = []     # aliases associados
    user_count: int = 0


class TableAssign(BaseModel):
    """Associa/dessasocia tabela a um perfil."""
    alias: str = Field(min_length=2, max_length=4)


class UserProfilesUpdate(BaseModel):
    """Substitui o conjunto de perfis vinculados ao usuario."""
    profile_codes: List[str] = []


# ---- Protheus Query -----------------------------------------------------------

OperatorLiteral = Literal[
    "eq", "ne", "gt", "gte", "lt", "lte", "like", "contains", "in", "between",
    "=", "<>", ">", ">=", "<", "<=",
    "igual", "diferente", "maior", "maior_igual", "menor", "menor_igual", "contem",
]


class FilterRule(BaseModel):
    """Uma regra de filtro vinda do construtor visual."""
    field: str = Field(min_length=2, max_length=60)
    op: OperatorLiteral = "eq"
    value: object = None        # str | int | list (in/between)


class JoinOn(BaseModel):
    """Uma condição do ON. Sprint 5 — Motor de Cruzamento.

    `left_alias`  : alias Protheus de QUALQUER tabela ja presente (base ou
                    junta anteriormente). Permite cascata: SC5 → SA1 → SA3.
    `left_column` : coluna naquela tabela (ex: "C5_CLIENTE").
    `right_column`: coluna da tabela que esta sendo juntada (alias da
                    JoinClause pai).

    Exemplo: SC5.C5_CLIENTE = SA1.A1_COD →
        JoinOn(left_alias="SC5", left_column="C5_CLIENTE", right_column="A1_COD")
    """
    left_alias: str = Field(min_length=2, max_length=4)
    left_column: str = Field(min_length=2, max_length=60)
    right_column: str = Field(min_length=2, max_length=60)


class JoinClause(BaseModel):
    """Uma tabela adicional juntada à base via JOIN. Sprint 5."""
    alias: str = Field(min_length=2, max_length=4)
    branch: str = Field(min_length=1, max_length=4)
    join_type: Literal["INNER", "LEFT"] = "INNER"
    on: List[JoinOn] = Field(min_length=1, description="1+ condicoes do ON (AND)")


# Compat: alias antigo `JoinSpec` mantido para nao quebrar imports legados.
class JoinSpec(BaseModel):
    """LEGADO — use JoinClause na Sprint 5 em diante."""
    alias: str
    branch: Optional[str] = None
    on: dict
    type: Literal["inner", "left"] = "inner"


class ProtheusQueryRequest(BaseModel):
    """Suporta três formatos:

    1. **JOIN multi-tabela (Sprint 5)**: `alias` + `branch` + `joins[]` (lista
       de `JoinClause`) + `columns` qualificadas (`SC5.C5_NUM`).
    2. Visual single (Fase 2+): `alias` + `branch` + `rules`.
    3. Cru (compat): `table` + `filters` (dict com sufixo `__op`).
    """
    # --- formato visual (single ou multi) ---
    alias: Optional[str] = Field(default=None, max_length=4)
    branch: Optional[str] = Field(default=None, max_length=4)
    rules: Optional[List[FilterRule]] = None
    # Sprint 5: lista de tabelas adicionais juntadas
    joins: Optional[List[JoinClause]] = None

    # --- formato cru / compat ---
    table: Optional[str] = Field(default=None, max_length=20)
    filters: Optional[dict] = None

    # --- comuns ---
    # `columns` aceita "FOO" (single) ou "ALIAS.FOO" (multi/JOIN).
    columns: Optional[List[str]] = None
    page: int = 1
    page_size: int = Field(default=100, le=5000)


class ProtheusQueryResponse(BaseModel):
    table: str
    rows: list
    total: int
    page: int
    page_size: int
    # Mapa {coluna_fisica: titulo_humano} via SX3 — frontend mostra o titulo no
    # cabecalho do grid e o nome fisico no tooltip.
    columns_human: dict = {}


# ---- Schedules ----------------------------------------------------------------

FileFormat = Literal["xlsx", "csv", "pdf", "ods"]


class ScheduleCreate(BaseModel):
    """Aceita o formato visual (`alias`+`branch`+`rules`) ou cru (`table_name`+`filters`).
    O backend converte tudo para o formato cru antes de gravar.
    """
    name: str
    # --- alias visual (preferido) ---
    alias: Optional[str] = Field(default=None, max_length=4)
    branch: Optional[str] = Field(default=None, max_length=4)
    rules: Optional[List[FilterRule]] = None
    # --- cru / compat ---
    table_name: Optional[str] = None
    filters: Optional[dict] = None
    # --- comuns ---
    columns: Optional[List[str]] = None
    file_format: FileFormat = "xlsx"
    recipients: List[EmailStr]
    cron: Optional[str] = None      # "0 8 * * 1-5"
    run_at: Optional[datetime] = None


class ScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    table_name: str
    file_format: str
    recipients: str
    cron: Optional[str]
    run_at: Optional[datetime]
    is_active: bool
    last_run_at: Optional[datetime]
    last_status: Optional[str]
    last_error: Optional[str]
    created_at: datetime


# ---- Audit --------------------------------------------------------------------

class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    timestamp: datetime
    username: Optional[str]
    action: str
    detail: Optional[str]
    ip_address: Optional[str]
    success: bool


# ---- Setup Wizard (Fase 3) ----------------------------------------------------

class SetupDbStep(BaseModel):
    """Conexao Protheus — entrada do passo 2 do Wizard."""
    db_url: str = Field(min_length=10)
    pool_size: int = Field(default=20, ge=1, le=100)
    max_overflow: int = Field(default=30, ge=0, le=200)


class SetupSmtpStep(BaseModel):
    host: str
    port: int = Field(default=587, ge=1, le=65535)
    user: str
    password: str
    sender: EmailStr
    use_tls: bool = True


class SetupBrandingStep(BaseModel):
    app_name: str = Field(min_length=2, max_length=80)
    primary_color: Optional[str] = Field(default=None, max_length=20)
    # logo vai por endpoint multipart separado


class SetupAdminStep(BaseModel):
    """Cria o primeiro admin se nao existir."""
    username: str = Field(min_length=3, max_length=60)
    email: EmailStr
    full_name: Optional[str] = None
    password: str = Field(min_length=8, max_length=72)


class SetupStateOut(BaseModel):
    setup_complete: bool
    completed_steps: List[str]
    has_admin: bool


# Resolve forward ref do TokenResponse -> UserOut
TokenResponse.model_rebuild()
